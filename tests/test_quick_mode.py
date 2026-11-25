"""이 파일이 확인하는 것: 빠른 검사(QUICK) 모드(11/3 MVP) 수용 시나리오. 대회 시연의 핵심 흐름이다.
흐름: 태블릿에서 기다리는 기기 고르기(또는 6자리 부스 코드) → 바닥(BOT) 찍기 → 뒤집어 윗면(TOP) 찍기(12장)
→ Jetson에서 흠집 탐지 → "문제 있음/없음" 판정. 수령·반납은 출고 때와 같은 검사 방식만 허용한다(11/13).

빠른 검사 모드(11/3 MVP) 수용 시나리오: 기기 목록 → BOT → 뒤집어 TOP → 탐지 → 문제 있음/없음.
"""

from __future__ import annotations

import json
import threading

import numpy as np
import pytest
from conftest import run_to_end
from fake_capture import BASE, SECRET, FakeCapture
from test_detect import SquareDetector
from test_web import call

from trace_booth.capture_client import ApiError, BoothAuth, Response
from trace_booth.codes import DEFECT_CLASSES
from trace_booth.detect.engine import DetectError, ModelInfo, RawDetection
from trace_booth.naming import parse_filename
from trace_booth.quick import QuickInspector, no_engine_source, result_json
from trace_booth.rentals import RentalClient, WaitingDevice
from trace_booth.session import InvalidInput
from trace_booth.shotlist import QUICK_SHOTLIST, StepKind
from trace_booth.web import App, make_server

DEVICE = "aAXQFO2Vz6ZTFbPctC2dq"  # rental-server 기기 publicId
RENTAL = "Ws0HLv6QRbDILPWOcz5I2"
CHECKPOINT = "h40b9AN9xMQhXRHrOyJWB"
NOW = "2026-11-05T10:00:00+09:00"
INFO = ModelInfo("yolo11s-seg-v001-trt-fp16", DEFECT_CLASSES)


# 테스트 도구: 검은 가짜 사진을 만들고, 이름이 맞는 사진에만 흰 네모(흠집 하나)를 넣는다.
def _loader(defect_on: str | None):
    """1920×1080 검은 사진(흑백). defect_on(파일명 일부)이 맞는 사진에만 흰 사각형(흠집 하나)."""
    def load(path: str):
        img = np.zeros((1080, 1920), dtype=np.uint8)  # SquareDetector는 흑백 한 장을 본다
        if defect_on and defect_on in path:
            img[100:130, 200:260] = 255
        return img
    return load


# 테스트 도구: 가짜 탐지기와 가짜 사진으로 빠른 검사 판정기를 만든다.
def _inspector(service, defect_on: str | None = "BOT_L1_CAM1", source=None) -> QuickInspector:
    return QuickInspector(service.store, source or (lambda: (SquareDetector(), INFO)), _loader(defect_on),
                          lambda: NOW, background=False)


# 테스트 도구: 빠른 검사 12장을 끝까지 찍고 완료한다.
def _shoot_quick(service):
    s = service.start(DEVICE, "QUICK", RENTAL, CHECKPOINT)
    run_to_end(service, s.id)
    return service.complete(s.id)


# [상황] 빠른 검사 목록을 본다.
# [기대] 기기 ID 판 없이, 바닥 3단계 → 뒤집기 안내 → 윗면 3단계, 두 카메라로 12장이다.
def test_빠른_검사_목록은_ID_카드_없이_BOT을_먼저_찍고_뒤집어_TOP을_찍는_12장이다():
    shots = [s for s in QUICK_SHOTLIST if s.kind is StepKind.SHOT]
    assert all(s.kind is not StepKind.SLATE for s in QUICK_SHOTLIST)
    assert sum(len(s.cameras) for s in shots) == 12
    assert [s.face.value for s in shots] == ["BOT"] * 3 + ["TOP"] * 3
    flip = next(i for i, s in enumerate(QUICK_SHOTLIST) if s.kind is StepKind.NOTICE)
    assert QUICK_SHOTLIST[flip - 1].face.value == "BOT" and QUICK_SHOTLIST[flip + 1].face.value == "TOP"


# [상황] 목록에서 고른 기기(21자 공개 ID)로 빠른 검사를 찍는다.
# [기대] 12장으로 완료되고, 파일 이름에서 기기 ID·면을 다시 읽을 수 있다.
def test_빠른_검사는_목록에서_고른_기기_publicId로_찍고_파일명을_다시_읽을_수_있다(service):
    done = _shoot_quick(service)
    shots = service.store.shots(done.id)
    assert len(shots) == 12 and done.status == "COMPLETE"
    parsed = parse_filename(shots[0].filename)
    assert parsed is not None and parsed.device_id == DEVICE and parsed.face.value == "BOT"


# [상황] 렌탈·시점 ID 없이 빠른 검사를 시작한다.
# [기대] 시작하지 않는다.
def test_빠른_검사는_렌탈_시점_ID가_없으면_시작하지_않는다(service):
    with pytest.raises(InvalidInput):
        service.start(DEVICE, "QUICK")


# [상황] 바닥 L1 CAM1 사진에만 흠집이 있다.
# [기대] "문제 있음"(DEFECT), 흠집 1개(바닥 1·윗면 0). 흠집 위치는 원본 사진 좌표와 사진 크기(1920×1080)로 돌려준다.
def test_흠집이_하나라도_있으면_문제_있음이고_폴리곤은_원본_px와_사진_크기로_돌려준다(service):
    done = _shoot_quick(service)
    _inspector(service).start(done.id)

    r = result_json(service.store, done.id, shooting=False)
    assert r["status"] == "DONE" and r["verdict"] == "DEFECT" and r["count"] == 1
    assert r["faces"] == {"BOT": 1, "TOP": 0} and r["modelVersion"] == INFO.model_version and not r["testOnly"]
    hit = [p for p in r["photos"] if p["detections"]]
    assert len(r["photos"]) == 12 and len(hit) == 1
    assert (hit[0]["face"], hit[0]["light"], hit[0]["camera"]) == ("BOT", "L1", "CAM1")
    assert (hit[0]["width"], hit[0]["height"]) == (1920, 1080) and hit[0]["shotId"]
    xs = [pt[0] for pt in hit[0]["detections"][0]["polygon"]]
    assert min(xs) == pytest.approx(200, abs=1) and max(xs) == pytest.approx(259, abs=1)


# [상황] 흠집이 하나도 없다.
# [기대] "문제 없음"(CLEAN).
def test_흠집이_없으면_문제_없음(service):
    done = _shoot_quick(service)
    _inspector(service, defect_on=None).start(done.id)
    r = result_json(service.store, done.id, shooting=False)
    assert r["verdict"] == "CLEAN" and r["count"] == 0


# 테스트 도구: 같은 자리를 오염(stain)으로 잡는 가짜 탐지기.
class StainDetector(SquareDetector):
    """SquareDetector와 같은 곳을 잡되 클래스를 오염(stain)으로 낸다."""

    def infer(self, tile) -> list[RawDetection]:
        return [RawDetection(DEFECT_CLASSES.index("stain"), d.confidence, d.polygon) for d in super().infer(tile)]


# [상황] 오염만 잡혔다.
# [기대] 위치는 남기되 판정은 "문제 없음"(오염은 닦으면 되므로 흠집으로 보지 않음).
def test_오염만_잡히면_폴리곤은_남기고_문제_없음으로_저장한다(service):
    done = _shoot_quick(service)
    _inspector(service, source=lambda: (StainDetector(), INFO)).start(done.id)
    r = result_json(service.store, done.id, shooting=False)
    assert r["status"] == "DONE" and r["verdict"] == "CLEAN" and r["count"] == 1
    hit = [d for p in r["photos"] for d in p["detections"]]
    assert len(hit) == 1 and hit[0]["class"] == "stain"


# [상황] 진짜 엔진 없이 시험 모드로 판정한다.
# [기대] "문제 없음"에 시험(testOnly) 표시가 붙는다.
def test_엔진_없이_시험하면_문제_없음에_시험_표시가_붙는다(service):
    done = _shoot_quick(service)
    _inspector(service, source=no_engine_source).start(done.id)
    r = result_json(service.store, done.id, shooting=False)
    assert r["verdict"] == "CLEAN" and r["testOnly"]


# [상황] 엔진 파일이 없어 탐지가 실패했다가, 엔진이 생긴다.
# [기대] 실패와 사유가 남고, 사진은 그대로라 다시 돌리면 정상 판정된다.
def test_엔진을_못_부르면_실패를_남기고_사진은_그대로_다시_돌릴_수_있다(service):
    done = _shoot_quick(service)
    tries = []

    def flaky():
        tries.append(1)
        if len(tries) == 1:
            raise DetectError("엔진 파일이 없음")
        return SquareDetector(), INFO

    inspector = _inspector(service, source=flaky)
    inspector.start(done.id)
    r = result_json(service.store, done.id, shooting=False)
    assert r["status"] == "FAILED" and r["verdict"] is None and "엔진 파일이 없음" in r["error"]

    inspector.start(done.id)
    assert result_json(service.store, done.id, shooting=False)["verdict"] == "DEFECT"


# [상황] 탐지가 끝난다.
# [기대] 다음 할 일(서버로 올리기)을 그 세션으로 부른다.
def test_탐지가_끝나면_다음_할_일을_부른다(service):
    done = _shoot_quick(service)
    called = []
    inspector = _inspector(service)
    inspector.after = called.append
    inspector.start(done.id)
    assert called == [done.id]


# --- 기기 목록(rental-server GET /api/v1/rentals) ---

# 테스트 도구: rental-server 렌탈 목록의 한 줄을 만든다.
def _rental(rid: str, cp_status: str | None, status: str = "OPEN") -> dict:
    cp = None if cp_status is None else {"checkpointId": CHECKPOINT, "type": "OUT", "status": cp_status,
                                          "occurredAt": None}
    return {"rentalId": rid, "deviceId": DEVICE, "serial": "C02EXAMPLE01", "modelCode": "MBA13-M2",
            "status": status, "createdAt": NOW, "currentCheckpoint": cp}


# 테스트 도구: 렌탈 목록을 두 쪽으로 나눠 주는 가짜 rental-server.
class FakeRentals(FakeCapture):
    """부스 토큰은 FakeCapture가, 렌탈 목록은 두 쪽으로 나눠 준다."""

    def __init__(self, pages: list[list[dict]]) -> None:
        super().__init__()
        self.pages = pages
        self.queries: list[str] = []

    def __call__(self, method, url, headers, body):
        if url.startswith(f"{BASE}/api/v1/rentals?"):
            self.calls.append((method, url))
            if headers.get("Authorization") != f"Bearer tok{self.token_count}":
                return self._problem(401, "UNAUTHORIZED")
            self.queries.append(url.split("?", 1)[1])
            n = 1 if "cursor=p2" in url else 0
            return Response(200, json.dumps({"items": self.pages[n],
                                             "nextCursor": "p2" if n + 1 < len(self.pages) else None}).encode())
        return super().__call__(method, url, headers, body)


# [상황] 진행 중 렌탈 목록이 두 쪽에 걸쳐 있고, 시점이 열린 것·닫힌 것이 섞여 있다.
# [기대] 열린 시점이 있는 렌탈만 다음 쪽까지 모아 기기·시리얼·기종·시점 정보로 보여 준다.
def test_기다리는_기기는_진행_중_렌탈의_열린_시점만_다음_쪽까지_모은다():
    server = FakeRentals([
        [_rental("R" * 21, "OPEN"), _rental("S" * 21, "CAPTURED"), _rental("T" * 21, None)],
        [_rental("U" * 21, "OPEN"), _rental("V" * 21, "OPEN", status="RETURNED")],
    ])
    client = RentalClient(BASE, BoothAuth(BASE, "booth-1", SECRET, http=server), http=server)

    waiting = client.waiting()

    assert [w.rental_id for w in waiting] == ["R" * 21, "U" * 21]
    assert waiting[0] == WaitingDevice("R" * 21, DEVICE, "C02EXAMPLE01", "MBA13-M2", CHECKPOINT, "OUT")
    assert "status=OPEN" in server.queries[0] and "cursor=p2" in server.queries[1]


# 테스트 도구: 부스 코드 305718만 맞는 가짜 rental-server.
class FakeBoothCode(FakeRentals):
    """rental-server 부스 코드 조회: 305718만 맞음"""

    def __init__(self) -> None:
        super().__init__([])
        self.bodies: list[dict] = []

    def __call__(self, method, url, headers, body):
        if url.endswith("/api/v1/rentals/booth-codes/redeem"):
            self.bodies.append(json.loads(body))
            if self.bodies[-1]["code"] != "305718":
                return Response(400, json.dumps({"code": "BOOTH_CODE_INVALID"}).encode())
            return Response(200, json.dumps({"rentalId": RENTAL, "deviceId": DEVICE, "serial": "C02EXAMPLE01",
                                             "modelCode": "MBA13-M2", "checkpointId": CHECKPOINT,
                                             "checkpointType": "OUT"}).encode())
        return super().__call__(method, url, headers, body)


# [상황] 관리자가 발급한 6자리 부스 코드를 넣는다. 틀린 코드, 5자리 코드도 넣는다.
# [기대] 맞으면 기기·렌탈·시점을 받고, 틀리면 400 BOOTH_CODE_INVALID, 5자리는 서버에 보내기 전에 거른다.
#        ※ 응답 코드: 400 = 요청 내용이 형식에 맞지 않음
def test_부스_코드를_rental_server에_보내_기기_렌탈_시점을_받는다():
    server = FakeBoothCode()
    client = RentalClient(BASE, BoothAuth(BASE, "booth-1", SECRET, http=server), http=server)

    assert client.redeem("305718") == WaitingDevice(RENTAL, DEVICE, "C02EXAMPLE01", "MBA13-M2", CHECKPOINT, "OUT")
    assert server.bodies == [{"code": "305718"}]
    with pytest.raises(ApiError) as e:
        client.redeem("111111")
    assert e.value.status == 400 and e.value.code == "BOOTH_CODE_INVALID"
    with pytest.raises(ValueError):
        client.redeem("12345")  # 서버에 보내기 전에 거른다
    assert len(server.bodies) == 2


# --- 로컬 API ---

@pytest.fixture
def quick_base(service):
    waiting = [WaitingDevice(RENTAL, DEVICE, "C02EXAMPLE01", "MBA13-M2", CHECKPOINT, "OUT")]
    app = App(service, quick=_inspector(service), waiting=lambda: waiting)
    server = make_server(app, "127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


# [상황] 부스 화면의 코드 입력 API를 여러 상황으로 부른다.
# [기대] 서버 설정이 없으면 503, 형식이 틀리면 400, 만료된 코드는 410과 "다시 발급하세요" 안내.
#        ※ 응답 코드: 503 = 서버를 지금 쓸 수 없음(점검·장애), 400 = 요청 내용이 형식에 맞지 않음, 410 = 이미 쓰였거나 만료되어 더는 쓸 수 없음
def test_부스_코드_API는_서버_설정이_없으면_503_형식이_틀리면_400_만료는_410으로_알린다(service):
    def redeem(code: str) -> WaitingDevice:
        raise ApiError("부스 코드", 410, "BOOTH_CODE_EXPIRED")

    for app, code, expected in ((App(service), "305718", 503), (App(service, redeem=redeem), "30571", 400),
                                (App(service, redeem=redeem), "305718", 410)):
        server = make_server(app, "127.0.0.1", 0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            status, _, body = call(f"http://127.0.0.1:{server.server_address[1]}", "POST", "/api/booth-code",
                                   {"code": code})
        finally:
            server.shutdown()
            server.server_close()
        assert status == expected
        if expected == 410:
            assert "다시 발급하세요" in json.loads(body)["detail"]


# [상황] 태블릿 화면에서 기기를 골라 빠른 검사를 찍고 완료한다.
# [기대] 기종이 보이고, 찍는 중엔 SHOOTING, 완료 뒤 결과 API가 "문제 있음" 판정을 준다. 미리보기도 열린다.
def test_태블릿에서_기기를_골라_찍고_완료하면_결과_API가_판정을_준다(quick_base):
    status, _, body = call(quick_base, "GET", "/api/rentals/waiting")
    item = json.loads(body)["items"][0]
    assert status == 200 and item["modelCode"] == "MBA13-M2"

    status, _, body = call(quick_base, "POST", "/api/sessions", {
        "mode": "QUICK", "deviceId": item["deviceId"], "rentalId": item["rentalId"],
        "checkpointId": item["checkpointId"]})
    assert status == 201 and json.loads(body)["quickEnabled"]
    status, _, body = call(quick_base, "GET", "/api/sessions/current/result")
    assert status == 200 and json.loads(body)["status"] == "SHOOTING"

    while (state := json.loads(call(quick_base, "GET", "/api/sessions/current")[2]))["step"]:
        if state["step"]["kind"] == "SHOT":
            assert call(quick_base, "POST", "/api/sessions/current/shoot")[0] == 201
        assert call(quick_base, "POST", "/api/sessions/current/next")[0] == 200
    assert call(quick_base, "POST", "/api/sessions/current/complete")[0] == 200

    status, _, body = call(quick_base, "GET", "/api/sessions/current/result")
    r = json.loads(body)
    assert status == 200 and r["status"] == "DONE" and r["verdict"] == "DEFECT"
    shot = next(p for p in r["photos"] if p["detections"])["shotId"]
    assert call(quick_base, "GET", f"/api/shots/{shot}/thumbnail")[0] == 200


# [상황] 서버 설정이 없거나, 탐지 엔진 설정이 없다.
# [기대] 기기 목록은 503, 엔진이 없으면 빠른 검사 시작을 400(TRACE_BOOTH_ENGINE 필요)으로 막는다.
#        ※ 응답 코드: 503 = 서버를 지금 쓸 수 없음(점검·장애), 400 = 요청 내용이 형식에 맞지 않음
def test_서버_설정이_없으면_기기_목록은_503이고_엔진이_없으면_빠른_검사를_막는다(service):
    server = make_server(App(service), "127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        assert call(base, "GET", "/api/rentals/waiting")[0] == 503
        status, _, body = call(base, "POST", "/api/sessions", {
            "mode": "QUICK", "deviceId": DEVICE, "rentalId": RENTAL, "checkpointId": CHECKPOINT})
        assert status == 400 and "TRACE_BOOTH_ENGINE" in json.loads(body)["detail"]
    finally:
        server.shutdown()
        server.server_close()


# [상황] 학습 데이터 촬영 세션에서 판정 결과를 묻는다.
# [기대] 409(판정이 없는 세션).
#        ※ 응답 코드: 409 = 지금 상태와 맞지 않아 거절(이미 있음·순서 어긋남 등)
def test_학습_모드_세션에는_결과_API가_409(quick_base):
    call(quick_base, "POST", "/api/sessions", {"deviceId": "MB01"})
    assert call(quick_base, "GET", "/api/sessions/current/result")[0] == 409


# [상황] 학습 데이터 모드에 21자 공개 ID 같은 값을 기기 ID로 준다.
# [기대] 거절한다(학습용은 MB01 같은 ID 카드 코드만).
def test_학습_데이터_모드는_ID_카드_코드만_받는다(service):
    with pytest.raises(InvalidInput):
        service.start(DEVICE, "TRAINING")


# --- 검사 방식 고정(11/13): 수령·반납은 출고와 같은 방식만 ---

# 테스트 도구: 렌탈의 출고 검사 방식을 정해 둔 값으로 답하는 가짜 capture-server.
class FakeProfile(FakeRentals):
    """capture-server 렌탈 검사 방식 조회: 출고를 profile로 했다고 답한다"""

    def __init__(self, profile: str | None) -> None:
        super().__init__([])
        self.profile = profile
        self.urls: list[str] = []

    def __call__(self, method, url, headers, body):
        if "/api/v1/captures/rentals/" in url:
            self.urls.append(url)
            return Response(200, json.dumps({"rentalId": RENTAL, "profile": self.profile,
                                             "checkpointId": "O" * 21 if self.profile else None}).encode())
        return super().__call__(method, url, headers, body)


# [상황] 렌탈의 검사 방식을 capture-server에 묻는다.
# [기대] 지금 시점을 빼고 앞 시점의 방식(FULL)을 받는다. 정해진 게 없으면 없음.
def test_렌탈_검사_방식은_capture_server에_지금_시점을_빼고_묻는다():
    server = FakeProfile("FULL")
    client = RentalClient(BASE, BoothAuth(BASE, "booth-1", SECRET, http=server), http=server)

    assert client.required_profile(RENTAL, CHECKPOINT) == "FULL"
    assert server.urls[0].endswith(f"/api/v1/captures/rentals/{RENTAL}/profile?checkpointId={CHECKPOINT}")
    server.profile = None
    assert client.required_profile(RENTAL, CHECKPOINT) is None


# 테스트 도구: 정해진 검사 방식을 답하는 부스 화면 서버를 띄운다.
def _profile_app(service, required: str | None, kind: str = "RCV", fail: bool = False):
    waiting = [WaitingDevice(RENTAL, DEVICE, "C02EXAMPLE01", "MBA13-M2", CHECKPOINT, kind)]

    def lookup(rental: str, checkpoint: str) -> str | None:
        if fail:
            raise ApiError("검사 방식", 404, "NOT_FOUND")
        return required

    app = App(service, quick=_inspector(service), waiting=lambda: waiting, required_profile=lookup)
    server = make_server(app, "127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


# [상황] 출고를 전체 검사로 한 기기를 수령 때 빠른 검사로 시작하려 한다.
# [기대] 기기 목록에 "전체 검사"가 실리고, 다른 방식은 400 "전체 검사로만"으로 막는다(같은 방식이어야 비교 가능).
#        ※ 응답 코드: 400 = 요청 내용이 형식에 맞지 않음
def test_수령_기기_목록에는_출고_때_방식이_실리고_다른_방식으로는_시작할_수_없다(service):
    server, base = _profile_app(service, "FULL")
    try:
        item = json.loads(call(base, "GET", "/api/rentals/waiting")[2])["items"][0]
        assert item["requiredProfile"] == "FULL"
        status, _, body = call(base, "POST", "/api/sessions", {
            "mode": "QUICK", "profile": "QUICK", "deviceId": DEVICE, "rentalId": RENTAL, "checkpointId": CHECKPOINT})
        assert status == 400 and "전체 검사로만" in json.loads(body)["detail"]
    finally:
        server.shutdown()
        server.server_close()


# [상황] 출고를 빠른 검사로 한 기기의 수령을 시작한다.
# [기대] 빠른 검사로 시작된다.
def test_출고를_빠른_검사로_했으면_수령도_빠른_검사로_시작된다(service):
    server, base = _profile_app(service, "QUICK")
    try:
        status, _, body = call(base, "POST", "/api/sessions", {
            "mode": "QUICK", "profile": "QUICK", "deviceId": DEVICE, "rentalId": RENTAL, "checkpointId": CHECKPOINT})
        assert status == 201 and json.loads(body)["profile"] == "QUICK"
    finally:
        server.shutdown()
        server.server_close()


# [상황] 출고 기기이거나, 서버가 방식 조회를 지원하지 않는 옛 버전이다.
# [기대] 정해진 방식 없이 보여 주고 시작을 막지 않는다.
def test_출고_기기는_방식을_묻지_않고_서버가_옛_버전이면_막지_않는다(service):
    server, base = _profile_app(service, "FULL", kind="OUT")
    try:
        assert json.loads(call(base, "GET", "/api/rentals/waiting")[2])["items"][0]["requiredProfile"] is None
    finally:
        server.shutdown()
        server.server_close()
    server, base = _profile_app(service, None, fail=True)
    try:
        assert json.loads(call(base, "GET", "/api/rentals/waiting")[2])["items"][0]["requiredProfile"] is None
        status, _, _ = call(base, "POST", "/api/sessions", {
            "mode": "QUICK", "deviceId": DEVICE, "rentalId": RENTAL, "checkpointId": CHECKPOINT})
        assert status == 201
    finally:
        server.shutdown()
        server.server_close()
