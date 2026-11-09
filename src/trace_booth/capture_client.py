"""출고·반납 모드의 서버 호출: 부스 JWT, 촬영 세트 생성, 사전 서명 URL PUT·다시 받기, 완료, 탐지 결과 (contracts C1).

HTTP는 `HttpCall` 함수 하나로 감싸 테스트에서 가짜 서버로 바꿔 끼운다. 사전 서명 URL과 비밀은 로그·오류 문구에 남기지 않는다.

연결(11/9 업로드 속도 개선):
- IPv4 주소부터 붙는다. 학교망 DNS64가 IPv6(64:ff9b::…)를 먼저 주는데 그 길이 막혀 연결마다 제한 시간을 다 쓰던 문제를
  /etc/gai.conf 없이도 피한다. 주소마다 연결 제한은 CONNECT_SECONDS로 짧게 둔다.
- 사전 서명 PUT은 스레드마다 연결을 다시 써서(keep-alive) 사진마다 TCP·TLS 연결을 새로 맺지 않는다.
- HTTP 프록시 환경 변수가 있으면 예전처럼 urllib으로 보낸다.
"""

from __future__ import annotations

import http.client
import json
import socket
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

TIMEOUT_SECONDS = 30
CONNECT_SECONDS = 5  # 주소 하나에 붙는 데 쓰는 최대 시간(막힌 IPv6에서 30초를 다 쓰지 않게)
USER_AGENT = "trace-booth"
TOKEN_MARGIN_SECONDS = 60  # 만료 1분 전에 다시 받는다


@dataclass
class Response:
    status: int
    body: bytes = b""

    def json(self) -> dict:
        return json.loads(self.body or b"{}")


# (method, url, headers, body) -> Response. 연결 실패는 OSError로 올린다.
HttpCall = Callable[[str, str, dict[str, str], bytes | None], Response]


def connect_ipv4_first(
    address: tuple[str, int], timeout: object = socket._GLOBAL_DEFAULT_TIMEOUT,  # type: ignore[attr-defined]
    source_address: tuple[str, int] | None = None, *_: object, **__: object,
) -> socket.socket:
    """socket.create_connection과 같지만 IPv4 주소를 먼저, 주소마다 CONNECT_SECONDS 안에 붙는다."""
    host, port = address
    infos = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
    infos.sort(key=lambda info: info[0] != socket.AF_INET)  # 같은 종류 안에서는 DNS 순서 그대로
    io_timeout = timeout if isinstance(timeout, (int, float)) else None
    connect_timeout = CONNECT_SECONDS if io_timeout is None else min(io_timeout, CONNECT_SECONDS)
    last: OSError | None = None
    for family, kind, proto, _, sockaddr in infos:
        sock = socket.socket(family, kind, proto)
        try:
            sock.settimeout(connect_timeout)
            if source_address is not None:
                sock.bind(source_address)
            sock.connect(sockaddr)
            sock.settimeout(io_timeout)
            return sock
        except OSError as e:
            last = e
            sock.close()
    raise last if last is not None else OSError(f"{host}의 주소를 찾지 못함")


_pool = threading.local()  # 스레드마다 (scheme, host, port) → 다시 쓸 연결


def _new_connection(scheme: str, host: str, port: int | None) -> http.client.HTTPConnection:
    cls = http.client.HTTPSConnection if scheme == "https" else http.client.HTTPConnection
    conn = cls(host, port, timeout=TIMEOUT_SECONDS)
    conn._create_connection = connect_ipv4_first  # type: ignore[attr-defined]  # http.client가 연결 때 부르는 훅
    return conn


def _uses_proxy(scheme: str, host: str) -> bool:
    return scheme in urllib.request.getproxies() and not urllib.request.proxy_bypass(host)


def _urllib_call(method: str, url: str, headers: dict[str, str], body: bytes | None) -> Response:
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as r:
            return Response(r.status, r.read())
    except urllib.error.HTTPError as e:
        return Response(e.code, e.read())


def http_call(method: str, url: str, headers: dict[str, str], body: bytes | None) -> Response:
    parts = urlsplit(url)
    host = parts.hostname or ""
    if parts.scheme not in ("http", "https") or not host or _uses_proxy(parts.scheme, host):
        return _urllib_call(method, url, headers, body)
    target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    send = {"User-Agent": USER_AGENT, **headers}
    reuse = method == "PUT"  # 사전 서명 PUT만 다시 쓴다(같은 PUT을 다시 보내도 안전). API POST는 늘 새 연결
    key = (parts.scheme, host, parts.port)
    conns: dict = _pool.__dict__.setdefault("conns", {})
    for attempt in (1, 2):
        conn = conns.pop(key, None) if reuse else None
        fresh = conn is None
        if conn is None:
            conn = _new_connection(parts.scheme, host, parts.port)
        try:
            conn.request(method, target, body=body, headers=send)
            r = conn.getresponse()
            data = r.read()
        except (OSError, http.client.HTTPException) as e:
            conn.close()
            if not fresh and attempt == 1:
                continue  # 서버가 쉬던 연결을 먼저 닫았을 수 있다: 새 연결로 한 번 더
            if isinstance(e, OSError):
                raise
            raise ConnectionError(type(e).__name__) from e  # 호출부는 연결 실패를 OSError로 받는다
        if reuse and not r.will_close:
            conns[key] = conn
        else:
            conn.close()
        return Response(r.status, data)
    raise AssertionError("도달하지 않음")


class ApiError(Exception):
    """서버가 2xx가 아닌 응답을 줌. code는 Problem의 codes.yaml error_code(없으면 None)."""

    def __init__(self, what: str, status: int, code: str | None) -> None:
        super().__init__(f"{what}: HTTP {status} {code or ''}".strip())
        self.status = status
        self.code = code


def _problem_code(resp: Response) -> str | None:
    try:
        return resp.json().get("code")
    except (ValueError, AttributeError):
        return None


def _check(what: str, resp: Response, ok: tuple[int, ...]) -> Response:
    if resp.status not in ok:
        raise ApiError(what, resp.status, _problem_code(resp))
    return resp


@dataclass
class PhotoSpec:
    face: str
    light: str
    camera: str
    sha256: str
    taken_at: str
    path: Path
    content_type: str = "image/jpeg"

    def to_json(self) -> dict[str, str]:
        return {
            "face": self.face, "light": self.light, "camera": self.camera, "sha256": self.sha256,
            "contentType": self.content_type, "takenAt": self.taken_at,
        }


@dataclass
class UploadTarget:
    face: str
    light: str
    camera: str
    url: str = field(repr=False)  # 사전 서명 URL은 repr·로그에 내지 않는다
    headers: dict[str, str]
    expires_at: str


class BoothAuth:
    """`POST /api/v1/auth/booth/token`. 토큰은 메모리에만 두고 만료 1분 전에 다시 받는다."""

    def __init__(
        self, base_url: str, client_id: str, client_secret: str,
        http: HttpCall = http_call, clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._url = base_url.rstrip("/") + "/api/v1/auth/booth/token"
        self._body = json.dumps({"clientId": client_id, "clientSecret": client_secret}).encode()
        self._http = http
        self._clock = clock
        self._token: str | None = None
        self._until = 0.0
        self.booth_id: str | None = None

    def token(self) -> str:
        if self._token is None or self._clock() >= self._until:
            resp = _check("부스 토큰", self._http("POST", self._url, {"Content-Type": "application/json"}, self._body),
                          (200,))
            data = resp.json()
            self._token = str(data["accessToken"])
            self._until = self._clock() + int(data["expiresIn"]) - TOKEN_MARGIN_SECONDS
            self.booth_id = data.get("boothId")
        return self._token

    def forget(self) -> None:
        """401을 받았을 때 다음 호출에서 새로 받게 한다."""
        self._token = None


def authed_call(
    http: HttpCall, auth: BoothAuth, what: str, method: str, url: str, data: bytes | None, ok: tuple[int, ...],
    extra: dict[str, str] | None = None,
) -> Response:
    """부스 JWT를 붙여 부른다. 401이면 토큰이 서버에서 먼저 만료됐을 수 있으므로 한 번만 새로 받아 다시 부른다."""
    for attempt in (1, 2):
        headers = {"Authorization": f"Bearer {auth.token()}", **(extra or {})}
        if data is not None:
            headers["Content-Type"] = "application/json"
        resp = http(method, url, headers, data)
        if resp.status == 401 and attempt == 1:
            auth.forget()
            continue
        return _check(what, resp, ok)
    raise AssertionError("도달하지 않음")


class CaptureClient:
    def __init__(self, base_url: str, auth: BoothAuth, http: HttpCall = http_call) -> None:
        self._base = base_url.rstrip("/") + "/api/v1/captures/sets"
        self._auth = auth
        self._http = http

    def _post(self, what: str, url: str, body: dict, ok: tuple[int, ...], extra: dict[str, str] | None = None) -> Response:
        return authed_call(self._http, self._auth, what, "POST", url, json.dumps(body).encode(), ok, extra)

    def create_set(
        self, rental_id: str, checkpoint_id: str, photos: list[PhotoSpec], profile: str | None = None,
    ) -> tuple[str, list[UploadTarget]]:
        """profile은 contracts 1.1.0 capture.profile(QUICK = 부스 빠른 검사). 빼면 서버가 FULL로 본다."""
        body: dict = {"rentalId": rental_id, "checkpointId": checkpoint_id, "source": "BOOTH",
                      "photos": [p.to_json() for p in photos]}
        if profile is not None:
            body["profile"] = profile
        data = self._post("촬영 세트 생성", self._base, body, (201,)).json()
        uploads = _targets(data)
        if len(uploads) != len(photos):
            raise ApiError("촬영 세트 생성: 업로드 URL 수가 사진 수와 다름", 201, None)
        for p, u in zip(photos, uploads):
            if (p.face, p.light, p.camera) != (u.face, u.light, u.camera):
                raise ApiError("촬영 세트 생성: 업로드 URL 순서가 사진과 다름", 201, None)
        return str(data["captureSetId"]), uploads

    def reissue_uploads(self, set_id: str, photos: list[PhotoSpec]) -> list[UploadTarget]:
        """`POST /captures/sets/{setId}/uploads`: 아직 안 올라간 사진의 URL만 다시 받는다(이미 올라간 사진은 빠져 온다)."""
        body = {"photos": [{"face": p.face, "light": p.light, "camera": p.camera} for p in photos]}
        return _targets(self._post("업로드 URL 다시 받기", f"{self._base}/{set_id}/uploads", body, (200,)).json())

    def save_detections(
        self, set_id: str, model_version: str, detections: list[dict], analyzed_photos: list[dict] | None = None,
        inference_ms: int | None = None, booth_total_ms: int | None = None,
    ) -> int:
        """`POST /captures/sets/{setId}/detections`: 부스 탐지 결과(원본 사진 px). 받아들인 개수를 돌려준다.

        다시 보내면 서버가 그 세트의 부스 탐지를 이 요청으로 통째로 바꾼다(Idempotency-Key 없음, 11/2 결정)."""
        body: dict = {"modelVersion": model_version, "detections": detections}
        if analyzed_photos is not None:  # QUICK 세트는 필수: 분석한 사진 전부(흠집 없는 사진 포함, contracts 1.1.0)
            body["analyzedPhotos"] = analyzed_photos
        if inference_ms is not None:  # 선택(contracts #29): 관리자 성능 그래프에 그대로 나간다
            body["inferenceMs"] = inference_ms
        if booth_total_ms is not None:  # 선택(contracts #30): 촬영 시작 버튼→부스 판정 ms
            body["boothTotalMs"] = booth_total_ms
        return int(self._post("탐지 결과 저장", f"{self._base}/{set_id}/detections", body, (201,)).json()["accepted"])

    def complete(self, set_id: str, skipped: dict[str, str], idempotency_key: str) -> dict:
        body = {"skippedFaces": skipped} if skipped else {}
        return self._post("촬영 완료", f"{self._base}/{set_id}/complete", body, (202,),
                          {"Idempotency-Key": idempotency_key}).json()


def _targets(data: dict) -> list[UploadTarget]:
    return [
        UploadTarget(u["face"], u["light"], u["camera"], u["url"], dict(u.get("headers") or {}), u["expiresAt"])
        for u in data["uploads"]
    ]


def put_photo(target: UploadTarget, photo: PhotoSpec, http: HttpCall = http_call) -> int:
    """사전 서명 URL로 PUT. 상태 코드를 돌려준다(URL은 오류 문구에 넣지 않는다)."""
    headers = {"Content-Type": photo.content_type, **target.headers}
    return http("PUT", target.url, headers, photo.path.read_bytes()).status
