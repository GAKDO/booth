"""운영 화면용 로컬 HTTP API (specs/001-booth-capture-tool/contracts/operator-web-api.md)."""

from __future__ import annotations

import hmac
import json
import threading
from collections.abc import Callable, Iterator
from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .camera import CameraError
from .camera_presets import PresetError
from .capture_client import ApiError
from .quick import QuickInspector
from .quick import result_json as _quick_result
from .quick_upload import upload_json
from .rentals import BOOTH_CODE_RE, WaitingDevice, redeem_message
from .session import CaptureError, CaptureService, Incomplete, InvalidInput
from .shotlist import (
    FULL_INSPECTION,
    PROFILE_FULL,
    PROFILE_QUICK,
    QUICK_SHOTLIST,
    SHOTLIST_LABELS,
    StepKind,
    inspection_profile,
    required_photo_count,
)
from .store import CheckoutRecord, ShotRecord

Submitter = Callable[[int], CheckoutRecord]
LiveFeed = Callable[[], Iterator[bytes]]  # 카메라 하나의 실시간 JPEG 프레임
WaitingList = Callable[[], list[WaitingDevice]]  # 빠른 검사의 검사 기다리는 기기
CodeRedeemer = Callable[[str], WaitingDevice]  # 부스 6자리 코드 → 기기·렌탈·시점


def result_json(store, session_id: int, shooting: bool) -> dict:
    out = _quick_result(store, session_id, shooting)
    out["upload"] = upload_json(store, session_id)
    return out


class App:
    """부스 하나에 촬영 세션 하나. 현재 세션 ID만 들고 있다."""

    def __init__(
        self,
        service: CaptureService,
        submitter: Submitter | None = None,
        live: dict[str, LiveFeed] | None = None,
        quick: QuickInspector | None = None,
        waiting: WaitingList | None = None,
        redeem: CodeRedeemer | None = None,
        ui_token: str | None = None,
    ) -> None:
        self.service = service
        # 운영 화면 접속 비밀(TRACE_BOOTH_UI_TOKEN). 있으면 쿠키나 X-Booth-Token 헤더가 맞아야 화면·API를 쓴다.
        # 비우면 예전처럼 누구나 쓴다(같은 망만 믿는 경우). 부스 자격(BOOTH JWT)을 대신 쓰는 화면이라 켜 두는 것을 권한다
        self.ui_token = ui_token or None
        self.live = live or {}  # 카메라 코드(CAM1·CAM2, 옆면 카메라는 SIDE) → 실시간 보기
        self.submitter = submitter  # 출고·반납 세트 올리기. 서버 설정이 없으면 None(출고·반납 모드를 막음)
        self.quick = quick  # 빠른 검사 탐지. 없으면 QUICK 모드를 막음
        self.waiting = waiting  # 렌탈 목록. 서버 설정이 없으면 None
        self.redeem = redeem  # 부스 코드 조회. 서버 설정이 없으면 None
        self.current: int | None = None  # 내부 id. 화면에는 publicId만 보낸다
        self.lock = threading.Lock()


def _shot_json(s: ShotRecord) -> dict:
    return {
        "id": s.public_id, "filename": s.filename, "face": s.face, "light": s.light, "camera": s.camera,
        "seq": s.seq, "sha256": s.sha256, "uploadStatus": s.upload_status,
    }


def _state(app: App) -> dict:
    v = app.service.view(app.current)
    step = None
    if v.step is not None:
        step = {
            "index": v.step.index, "kind": v.step.kind.value, "instruction": v.step.instruction,
            "face": v.step.face.value if v.step.face else None,
            "light": v.step.light.value if v.step.light else None,
            "cameras": [c.value for c in v.step.cameras], "optional": v.step.optional, "taken": v.taken,
            # 안내 단계 다음에 찍을 면(화면 진행 칸 표시용). 뒤에 찍을 단계가 없으면 None
            "nextFace": next((st.face.value for st in app.service.steps_for(v.session)[v.step.index:]
                              if st.kind is StepKind.SHOT and st.face is not None), None),
        }
    return {
        "sessionId": v.session.public_id, "deviceId": v.session.device_id, "mode": v.session.mode,
        "status": v.session.status, "step": step, "remainingSteps": v.remaining_steps,
        "missing": v.missing, "scr1SkipReason": v.session.scr1_skip_reason,
        "shotlist": v.session.shotlist or app.service.default_list,
        "profile": inspection_profile(v.session.shotlist) if v.session.mode == "QUICK" else None,
        "queue": app.service.store.upload_counts(), "presetsConfigured": app.service.presets.configured,
        "checkout": _checkout_json(app.service.store.get_checkout(v.session.id)),
        "checkoutEnabled": app.submitter is not None,
        "quickEnabled": app.quick is not None, "waitingEnabled": app.waiting is not None,
    }


def _checkout_json(c: CheckoutRecord | None) -> dict | None:
    if c is None:
        return None
    return {
        "rentalId": c.rental_id, "checkpointId": c.checkpoint_id, "captureSetId": c.capture_set_id,
        "status": c.status, "attempts": c.attempts, "lastError": c.last_error,
    }


_UI_COOKIE = "trace_booth_ui"


def _cookie(header: str | None, name: str) -> str | None:
    """Cookie 헤더에서 이름 하나의 값을 찾는다(없으면 None)."""
    for part in (header or "").split(";"):
        key, _, value = part.strip().partition("=")
        if key == name:
            return value
    return None


def make_handler(app: App) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: object) -> None:  # 요청 로그는 남기지 않음
            pass

        def do_GET(self) -> None:
            self._dispatch("GET")

        def do_POST(self) -> None:
            self._dispatch("POST")

        def _dispatch(self, method: str) -> None:
            try:
                path = self.path.split("?", 1)[0]
                if not self._allowed(method, path):
                    return
                self._route(method, path)
            except Incomplete as e:
                self._problem(409, str(e), missing=e.missing)
            except CaptureError as e:
                self._problem(e.status, str(e))
            except CameraError as e:
                self._problem(503, f"카메라 실패: {e.reason}", camera=e.camera.value)
            except PresetError as e:
                self._problem(500, str(e))
            except (ValueError, KeyError, TypeError) as e:
                self._problem(400, f"잘못된 요청: {e}")

        def _allowed(self, method: str, path: str) -> bool:
            """요청을 받기 전 검사. 막았으면 응답을 쓰고 False를 돌려준다.

            - POST는 JSON만 받는다. text/plain 같은 '단순 요청'은 브라우저가 사전 요청 없이 다른 사이트에서도 보낼 수 있어
              (CSRF) 부스 자격으로 촬영 세트를 만들 수 있었다. JSON 요청은 다른 출처면 브라우저가 사전 요청을 먼저 보낸다.
            - Origin이 있으면 이 화면 주소(Host)와 같아야 한다.
            - 접속 비밀이 설정돼 있으면 쿠키나 헤더가 맞아야 한다. 첫 화면은 /?token=<비밀>로 열면 쿠키를 심는다.
            """
            if method == "POST":
                ctype = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
                if ctype != "application/json":
                    self._problem(415, "JSON 요청만 받아요")
                    return False
                origin = self.headers.get("Origin")
                if origin is not None and urlsplit(origin).netloc != (self.headers.get("Host") or ""):
                    self._problem(403, "다른 주소에서 온 요청은 받지 않아요")
                    return False
            token = app.ui_token
            if token is None or self._has_token(token):
                return True
            if method == "GET" and path == "/":
                given = parse_qs(urlsplit(self.path).query).get("token", [""])[0]
                if given and hmac.compare_digest(given.encode(), token.encode()):
                    # 주소창에서 비밀을 지우고 쿠키로 다시 연다
                    self.send_response(303)
                    self.send_header("Location", "/")
                    self.send_header("Set-Cookie", f"{_UI_COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return False
            self._problem(401, "부스 화면 접속 비밀이 필요해요. 부스 콘솔에 보이는 주소(/?token=...)로 여세요")
            return False

        def _has_token(self, token: str) -> bool:
            given = self.headers.get("X-Booth-Token") or _cookie(self.headers.get("Cookie"), _UI_COOKIE) or ""
            return bool(given) and hmac.compare_digest(given.encode(), token.encode())

        def _route(self, method: str, path: str) -> None:
            svc = app.service
            if method == "GET" and path == "/":
                body = resources.files("trace_booth").joinpath("static/index.html").read_bytes()
                return self._send(200, body, "text/html; charset=utf-8")
            if method == "GET" and path == "/api/queue":
                return self._json(200, svc.store.upload_counts())
            if method == "GET" and path == "/api/shotlists":
                # 학습 화면의 '촬영 목록' 고르기. 기본은 서버를 켤 때 TRACE_BOOTH_SHOTLIST로 고른 것
                lists = [
                    {"name": name, "label": SHOTLIST_LABELS.get(name, name), "photos": required_photo_count(steps)}
                    for name, steps in svc.training_lists.items()
                ]
                return self._json(200, {"default": svc.default_list, "lists": lists})
            if method == "GET" and path == "/api/inspection-profiles":
                # 검사 모드의 세트 범위 고르기(11/8). 기본은 서버를 켤 때 TRACE_BOOTH_INSPECTION으로 고른 것(없으면 빠른 검사)
                profiles = [{"name": PROFILE_QUICK, "label": "빠른 검사(위·아래)",
                             "photos": required_photo_count(svc.mode_steps.get("QUICK", QUICK_SHOTLIST))}]
                if FULL_INSPECTION in svc.inspection_lists:
                    profiles.append({"name": PROFILE_FULL, "label": "전체 검사(위·아래·키보드·옆면)",
                                     "photos": required_photo_count(svc.inspection_lists[FULL_INSPECTION])})
                return self._json(200, {"default": svc.default_profile, "profiles": profiles})
            if method == "GET" and path.startswith("/api/shots/") and path.endswith("/thumbnail"):
                shot = svc.store.get_shot(path.split("/")[3])
                if shot is None:
                    return self._problem(404, "사진이 없음")
                return self._send(200, _thumbnail(Path(shot.local_path), _thumb_width(self.path)), "image/jpeg")
            if method == "GET" and path == "/api/rentals/waiting":
                if app.waiting is None:
                    return self._problem(503, "서버 연결 설정(TRACE_API_URL·부스 자격)이 없어 기기 목록을 못 불러와요")
                try:
                    items = app.waiting()
                except (ApiError, OSError, ValueError, KeyError, TypeError) as e:  # 서버 응답 문제는 502
                    return self._problem(502, f"기기 목록을 못 불러왔어요: {type(e).__name__}: {e}")
                return self._json(200, {"items": [w.to_json() for w in items]})
            if method == "POST" and path == "/api/booth-code":
                if app.redeem is None:
                    return self._problem(503, "서버 연결 설정(TRACE_API_URL·부스 자격)이 없어 부스 코드를 못 써요")
                code = str(self._body().get("code", "")).strip()
                if not BOOTH_CODE_RE.match(code):
                    return self._problem(400, "부스 코드는 숫자 6자리예요")
                try:
                    target = app.redeem(code)
                except ApiError as e:  # 서버가 거절한 이유를 부스 문장으로(틀림 400·만료 410·막힘 429)
                    return self._problem(e.status if e.status in (400, 410, 429) else 502, redeem_message(e))
                except (OSError, ValueError, KeyError, TypeError) as e:
                    return self._problem(502, f"부스 코드를 확인하지 못했어요: {type(e).__name__}: {e}")
                return self._json(200, target.to_json())
            if method == "GET" and path == "/api/live":
                return self._json(200, {"cameras": list(app.live)})  # 화면이 보일 카메라(옆면 Brio는 SIDE)
            if method == "GET" and path.startswith("/api/live/"):
                feed = app.live.get(path[len("/api/live/"):])
                if feed is None:
                    return self._problem(404, "실시간 보기를 쓸 수 없는 카메라")
                return self._stream(feed())
            if method == "POST" and path == "/api/sessions":
                body = self._body()
                mode = str(body.get("mode", "TRAINING"))
                if mode == "QUICK" and app.quick is None:
                    raise InvalidInput("탐지 엔진 설정(TRACE_BOOTH_ENGINE)이 없어 빠른 검사를 쓸 수 없어요")
                if mode in ("OUT", "RET") and app.submitter is None:
                    raise InvalidInput("서버 연결 설정(TRACE_API_URL·부스 자격)이 없어 출고·반납 모드를 쓸 수 없어요")
                rental, checkpoint = body.get("rentalId"), body.get("checkpointId")
                with app.lock:
                    shotlist, profile = body.get("shotlist"), body.get("profile")
                    app.current = svc.start(
                        str(body.get("deviceId", "")), mode,
                        str(rental) if rental else None, str(checkpoint) if checkpoint else None,
                        str(shotlist) if shotlist else None, str(profile) if profile else None,
                    ).id
                return self._json(201, _state(app))
            if not path.startswith("/api/sessions/current"):
                return self._problem(404, "없는 경로")
            if app.current is None:
                return self._problem(404, "시작한 촬영이 없음")
            action = path[len("/api/sessions/current"):]
            if method == "GET" and action == "":
                return self._json(200, _state(app))
            if method == "GET" and action == "/result":
                session = svc.store.get_session(app.current)
                if session.mode != "QUICK":
                    return self._problem(409, "빠른 검사 촬영이 아니에요")
                return self._json(200, result_json(svc.store, session.id, session.status == "IN_PROGRESS"))
            if method != "POST":
                return self._problem(405, "허용하지 않는 메서드")
            if action in ("/shoot", "/retake"):
                shots = svc.shoot(app.current) if action == "/shoot" else svc.retake(app.current)
                if app.quick is not None and svc.store.get_session(app.current).mode == "QUICK":
                    app.quick.prefetch(app.current)  # 찍는 동안 미리 탐지(완료 때는 남은 사진만)
                return self._json(201, {"shots": [_shot_json(s) for s in shots], "state": _state(app)})
            if action == "/next":
                expected = self._body().get("step")
                svc.advance(app.current, expected if isinstance(expected, int) else None)
                return self._json(200, _state(app))
            if action == "/skip-scr1":
                svc.skip_optional(app.current, str(self._body().get("reason", "")))
                return self._json(200, _state(app))
            if action == "/complete":
                done = svc.complete(app.current)
                if done.mode == "QUICK" and app.quick is not None:
                    app.quick.start(done.id)  # 바로 돌아오고, 화면은 /result를 다시 읽는다
                elif done.mode != "TRAINING" and app.submitter is not None:
                    app.submitter(done.id)
                return self._json(200, {"session": asdict(done), "state": _state(app)})
            if action == "/detect":
                session = svc.store.get_session(app.current)
                if session.mode != "QUICK" or session.status != "COMPLETE" or app.quick is None:
                    raise CaptureError("완료한 빠른 검사만 다시 탐지할 수 있어요")
                app.quick.start(session.id)
                return self._json(202, result_json(svc.store, session.id, False))
            if action == "/submit":
                if app.submitter is None:
                    raise InvalidInput("서버 연결 설정이 없어요")
                app.submitter(app.current)
                return self._json(200, _state(app))
            return self._problem(404, "없는 경로")

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            data = json.loads(self.rfile.read(n) or b"{}")
            if not isinstance(data, dict):
                raise TypeError("JSON 객체가 아님")
            return data

        def _json(self, status: int, data: dict) -> None:
            self._send(status, json.dumps(data, ensure_ascii=False).encode(), "application/json")

        def _problem(self, status: int, detail: str, **extra: object) -> None:
            body = {"type": "about:blank", "title": HTTPStatus(status).phrase, "status": status, "detail": detail}
            body.update(extra)
            self._send(status, json.dumps(body, ensure_ascii=False).encode(), "application/problem+json")

        def _stream(self, frames: Iterator[bytes]) -> None:
            """multipart/x-mixed-replace로 JPEG를 이어 보낸다. 받는 쪽이 끊으면 장치를 닫는다."""
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                for jpg in frames:
                    self.wfile.write(
                        b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                        + str(len(jpg)).encode() + b"\r\n\r\n" + jpg + b"\r\n"
                    )
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                close = getattr(frames, "close", None)
                if close is not None:
                    close()
            self.close_connection = True

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    return Handler


def _thumb_width(raw_path: str) -> int:
    """?w=긴 변 픽셀(선택). 판정 화면의 큰 사진용. 없거나 이상하면 480, 160~1920로 묶음."""
    w = parse_qs(urlsplit(raw_path).query).get("w", [""])[0]
    return max(160, min(1920, int(w))) if w.isdigit() else 480


def _thumbnail(path: Path, long_side: int = 480) -> bytes:
    data = path.read_bytes()
    try:
        import cv2
        import numpy as np
    except ImportError:
        return data
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return data
    h, w = img.shape[:2]
    scale = long_side / max(h, w)
    if scale < 1:
        img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return buf.tobytes() if ok else data


def make_server(app: App, host: str, port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), make_handler(app))
