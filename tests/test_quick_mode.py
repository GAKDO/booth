"""빠른 검사 모드(11/3 MVP) 수용 시나리오: 기기 목록 → BOT → 뒤집어 TOP → 탐지 → 문제 있음/없음."""

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


def _loader(defect_on: str | None):
    """1920×1080 검은 사진(흑백). defect_on(파일명 일부)이 맞는 사진에만 흰 사각형(흠집 하나)."""
    def load(path: str):
        img = np.zeros((1080, 1920), dtype=np.uint8)  # SquareDetector는 흑백 한 장을 본다
        if defect_on and defect_on in path:
            img[100:130, 200:260] = 255
        return img
    return load


def _inspector(service, defect_on: str | None = "BOT_L1_CAM1", source=None) -> QuickInspector:
    return QuickInspector(service.store, source or (lambda: (SquareDetector(), INFO)), _loader(defect_on),
                          lambda: NOW, background=False)


def _shoot_quick(service):
    s = service.start(DEVICE, "QUICK", RENTAL, CHECKPOINT)
    run_to_end(service, s.id)
    return service.complete(s.id)


def test_빠른_검사_목록은_ID_카드_없이_BOT을_먼저_찍고_뒤집어_TOP을_찍는_12장이다():
    shots = [s for s in QUICK_SHOTLIST if s.kind is StepKind.SHOT]
    assert all(s.kind is not StepKind.SLATE for s in QUICK_SHOTLIST)
    assert sum(len(s.cameras) for s in shots) == 12
    assert [s.face.value for s in shots] == ["BOT"] * 3 + ["TOP"] * 3
    flip = next(i for i, s in enumerate(QUICK_SHOTLIST) if s.kind is StepKind.NOTICE)
    assert QUICK_SHOTLIST[flip - 1].face.value == "BOT" and QUICK_SHOTLIST[flip + 1].face.value == "TOP"


def test_빠른_검사는_목록에서_고른_기기_publicId로_찍고_파일명을_다시_읽을_수_있다(service):
    done = _shoot_quick(service)
    shots = service.store.shots(done.id)
    assert len(shots) == 12 and done.status == "COMPLETE"
    parsed = parse_filename(shots[0].filename)
    assert parsed is not None and parsed.device_id == DEVICE and parsed.face.value == "BOT"


def test_빠른_검사는_렌탈_시점_ID가_없으면_시작하지_않는다(service):
    with pytest.raises(InvalidInput):
        service.start(DEVICE, "QUICK")


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


def test_흠집이_없으면_문제_없음(service):
    done = _shoot_quick(service)
    _inspector(service, defect_on=None).start(done.id)
    r = result_json(service.store, done.id, shooting=False)
    assert r["verdict"] == "CLEAN" and r["count"] == 0


class StainDetector(SquareDetector):
    """SquareDetector와 같은 곳을 잡되 클래스를 오염(stain)으로 낸다."""

    def infer(self, tile) -> list[RawDetection]:
        return [RawDetection(DEFECT_CLASSES.index("stain"), d.confidence, d.polygon) for d in super().infer(tile)]


def test_오염만_잡히면_폴리곤은_남기고_문제_없음으로_저장한다(service):
    done = _shoot_quick(service)
    _inspector(service, source=lambda: (StainDetector(), INFO)).start(done.id)
    r = result_json(service.store, done.id, shooting=False)
    assert r["status"] == "DONE" and r["verdict"] == "CLEAN" and r["count"] == 1
    hit = [d for p in r["photos"] for d in p["detections"]]
    assert len(hit) == 1 and hit[0]["class"] == "stain"


def test_엔진_없이_시험하면_문제_없음에_시험_표시가_붙는다(service):
    done = _shoot_quick(service)
    _inspector(service, source=no_engine_source).start(done.id)
    r = result_json(service.store, done.id, shooting=False)
    assert r["verdict"] == "CLEAN" and r["testOnly"]


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


def test_탐지가_끝나면_다음_할_일을_부른다(service):
    done = _shoot_quick(service)
    called = []
    inspector = _inspector(service)
    inspector.after = called.append
    inspector.start(done.id)
    assert called == [done.id]


# --- 기기 목록(rental-server GET /api/v1/rentals) ---

def _rental(rid: str, cp_status: str | None, status: str = "OPEN") -> dict:
    cp = None if cp_status is None else {"checkpointId": CHECKPOINT, "type": "OUT", "status": cp_status,
                                          "occurredAt": None}
    return {"rentalId": rid, "deviceId": DEVICE, "serial": "C02EXAMPLE01", "modelCode": "MBA13-M2",
            "status": status, "createdAt": NOW, "currentCheckpoint": cp}


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


def test_학습_모드_세션에는_결과_API가_409(quick_base):
    call(quick_base, "POST", "/api/sessions", {"deviceId": "MB01"})
    assert call(quick_base, "GET", "/api/sessions/current/result")[0] == 409


def test_학습_데이터_모드는_ID_카드_코드만_받는다(service):
    with pytest.raises(InvalidInput):
        service.start(DEVICE, "TRAINING")
