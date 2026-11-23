# 이 파일이 확인하는 것: 부스 흠집 탐지(BO2 스토리 1) 수용 시나리오. 1920×1080 사진을 640px 조각으로 잘라 탐지하고,
# 조각 좌표를 원본 사진 좌표로 되돌리며, 겹친 조각의 같은 흠집은 하나로 합친다.
# detect 명령은 결과를 부스에 JSON으로 남기고, 서버에 올린 세트가 있으면 탐지 결과도 보낸다. 탐지기는 가짜다.

"""BO2 스토리 1 수용 시나리오: 가짜 탐지기로 타일·좌표·중복 제거·세트 결과를 확인한다."""

from __future__ import annotations

import itertools
import json

import numpy as np
import pytest
from conftest import run_to_end

from trace_booth.detect.engine import DetectError, ModelInfo, RawDetection
from trace_booth.detect.merge import Found, dedupe
from trace_booth.detect.runner import detect_photo, detect_set
from trace_booth.detect.tiling import Tile, tiles, to_image

INFO = ModelInfo("yolo11s-seg-v001-trt-fp16", ("scratch", "dent", "crack", "stain"))


# 테스트 도구: 하얀(255) 픽셀 덩어리를 흠집 상자 하나로 돌려주는 가짜 탐지기.
class SquareDetector:
    """이미지에서 값이 255인 픽셀 덩어리를 상자 폴리곤 하나로 돌려준다(클래스 0, 신뢰도는 보이는 넓이에 비례)."""

    def __init__(self) -> None:
        self.calls = 0

    def infer(self, tile) -> list[RawDetection]:
        self.calls += 1
        ys, xs = np.nonzero(tile == 255)
        if len(xs) == 0:
            return []
        x0, x1, y0, y1 = float(xs.min()), float(xs.max()), float(ys.min()), float(ys.max())
        return [RawDetection(0, min(1.0, len(xs) / 400), [(x0, y0), (x1, y0), (x1, y1), (x0, y1)])]


# [상황] 1920×1080 사진을 조각으로 나눈다.
# [기대] 640×640 조각들이 빈틈없이 덮고, 이웃끼리 20% 이상 겹친다.
def test_1920x1080_사진을_타일로_자르면_전체를_빠짐없이_덮고_이웃_타일이_겹친다():
    ts = tiles(1920, 1080, size=640, overlap=0.2)
    covered = np.zeros((1080, 1920), dtype=bool)
    for t in ts:
        covered[t.y:t.y + t.h, t.x:t.x + t.w] = True
    assert covered.all()
    xs = sorted({t.x for t in ts})
    assert all(b - a <= 640 - 128 for a, b in itertools.pairwise(xs))  # 20% 이상 겹침
    assert all(t.w == 640 and t.h == 640 for t in ts)


# [상황] 조각 안의 흠집 좌표를 원본 좌표로 바꾼다.
# [기대] 조각 위치만큼 옮겨지고 사진 안에 있다.
def test_타일_안의_폴리곤을_원본_좌표로_되돌리면_사진_안에_있다():
    poly = to_image([(10, 20), (700, 20), (700, 50)], Tile(1280, 440, 640, 640), 1920, 1080)
    assert poly[0] == [1290.0, 460.0]
    assert all(0 <= x <= 1920 and 0 <= y <= 1080 for x, y in poly)


# [상황] 두 조각이 같은 흠집을 잡았고, 다른 종류 흠집도 하나 있다.
# [기대] 같은 흠집은 신뢰도 높은 하나만 남고, 다른 종류는 그대로 남는다.
def test_겹치는_두_타일이_같은_흠집을_잡으면_신뢰도가_높은_하나만_남는다():
    a = Found("scratch", [[100, 100], [140, 100], [140, 120], [100, 120]], 0.6)
    b = Found("scratch", [[102, 101], [141, 101], [141, 121], [102, 121]], 0.9)
    c = Found("dent", [[100, 100], [140, 100], [140, 120], [100, 120]], 0.5)  # 다른 클래스는 남김
    assert dedupe([a, b, c]) == [b, c]


# [상황] 두 조각이 겹치는 경계에 흠집이 있다.
# [기대] 원본에서는 흠집 하나로, 정확한 위치로 잡힌다.
def test_타일_경계에_걸친_흠집도_원본에서는_하나로_잡힌다():
    image = np.zeros((1080, 1920), dtype=np.uint8)
    image[300:320, 500:540] = 255  # 첫째·둘째 타일이 겹치는 x 512 부근
    found = detect_photo(image, SquareDetector(), INFO)
    assert len(found) == 1
    assert found[0].defect_class == "scratch"
    x0, y0, x1, y1 = found[0].box
    assert (x0, y0, x1, y1) == (500.0, 300.0, 539.0, 319.0)


# [상황] 흠집이 한 조각엔 10px만, 옆 조각엔 전부 들어 있다.
# [기대] 잘린 조각은 따로 세지 않고 하나로 잡힌다.
def test_타일_끝에_조금만_걸려_잘린_조각은_따로_세지_않는다():
    image = np.zeros((1080, 1920), dtype=np.uint8)
    image[500:520, 630:660] = 255  # 첫째 타일(0~639)에는 10px만, 둘째 타일(512~)에는 전부
    found = detect_photo(image, SquareDetector(), INFO)
    assert len(found) == 1
    assert found[0].box == (630.0, 500.0, 659.0, 519.0)


# [상황] 한 세션의 사진 전체를 탐지한다.
# [기대] 기기 ID 판(SLATE)은 빼고, 사진마다 면·조명·카메라와 모델 버전·걸린 시간이 기록된다.
def test_세트를_탐지하면_결과마다_면_조명_카메라와_modelVersion이_있고_SLATE는_탐지하지_않는다(service):
    session = service.start("MB01")
    run_to_end(service, session.id)
    shots = service.store.shots(session.id)
    detector = SquareDetector()
    image = np.zeros((1080, 1920), dtype=np.uint8)
    image[10:30, 10:30] = 255
    ticks = iter([0.0, 12.5])

    result = detect_set(shots, detector, INFO, load=lambda _p: image, clock=lambda: next(ticks))

    face_shots = [s for s in shots if s.kind == "SHOT"]
    assert len(result.photos) == len(face_shots) and len(face_shots) < len(shots)  # SLATE 빠짐
    assert result.model_version == "yolo11s-seg-v001-trt-fp16" and result.seconds == 12.5
    assert {(p.face, p.light, p.camera) for p in result.photos} == {(s.face, s.light, s.camera) for s in face_shots}
    assert all(len(p.found) == 1 for p in result.photos)


# [상황] model.json의 종류 목록이 codes.yaml과 다르거나 모델 버전이 없다.
# [기대] 시작하지 않는다(순서만 다른 같은 종류 목록은 읽힌다).
def test_모델_클래스가_codes와_다르거나_modelVersion이_없으면_시작하지_않는다(tmp_path):
    path = tmp_path / "model.json"
    path.write_text(json.dumps({"modelVersion": "v1", "classes": ["scratch", "dent", "crack", "dust"]}))
    with pytest.raises(DetectError, match="codes.yaml"):
        ModelInfo.load(path)
    path.write_text(json.dumps({"classes": ["scratch", "dent", "crack", "stain"]}))
    with pytest.raises(DetectError, match="modelVersion"):
        ModelInfo.load(path)
    path.write_text(json.dumps({"modelVersion": "v1", "classes": ["stain", "crack", "dent", "scratch"]}))
    assert ModelInfo.load(path).classes == ("stain", "crack", "dent", "scratch")


# 테스트 도구: 가짜 엔진 파일과 model.json을 만든다.
def _engine(tmp_path, classes=("scratch", "dent", "crack", "stain")):
    engine = tmp_path / "models" / "best.engine"
    engine.parent.mkdir()
    engine.write_bytes(b"fake")
    (engine.parent / "model.json").write_text(json.dumps({"modelVersion": "v001", "classes": list(classes)}))
    return engine


# [상황] detect 명령으로 세션 사진을 탐지한다.
# [기대] 28장 결과가 사진 좌표 기준 JSON으로 남고, 원본 사진은 한 바이트도 바뀌지 않는다(증거 보존).
def test_detect_명령은_세션_사진을_탐지해_로컬_JSON으로_남기고_원본은_그대로_둔다(service, tmp_path, monkeypatch):
    from trace_booth.__main__ import detect

    monkeypatch.setenv("TRACE_BOOTH_DATA_DIR", str(tmp_path))  # service 픽스처의 booth.db와 같은 곳
    s = service.start("MB01")
    run_to_end(service, s.id, skip_scr1="화면이 켜지지 않음")
    before = {p.local_path: p.sha256 for p in service.store.shots(s.id)}
    image = np.zeros((1080, 1920), dtype=np.uint8)
    image[100:120, 100:130] = 255

    code = detect(s.public_id, str(_engine(tmp_path)), make_detector=lambda _: SquareDetector(), load=lambda _: image)

    assert code == 0
    out = json.loads(next((tmp_path / "MB01" / "detections").glob(f"{s.public_id}_v001.json")).read_text())
    assert out["modelVersion"] == "v001" and out["coordinates"] == "photo_px"
    assert len(out["photos"]) == 28 and out["count"] == 28
    assert out["photos"][0]["detections"][0]["class"] == "scratch"
    import hashlib
    from pathlib import Path

    assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest() == h for p, h in before.items())


# [상황] 모델 종류 목록이 다른 엔진으로 detect를 실행한다.
# [기대] "탐지를 시작할 수 없어요"로 멈춘다.
def test_detect_명령은_모델_클래스가_다르면_시작하지_않는다(service, tmp_path, monkeypatch):
    from trace_booth.__main__ import detect

    monkeypatch.setenv("TRACE_BOOTH_DATA_DIR", str(tmp_path))  # service 픽스처의 booth.db와 같은 곳
    s = service.start("MB01")
    with pytest.raises(SystemExit, match="탐지를 시작할 수 없어요"):
        detect(s.public_id, str(_engine(tmp_path, ("scratch",))), make_detector=lambda _: SquareDetector())


# [상황] 윗면 L1을 다시 찍었다.
# [기대] 다시 찍은 마지막 장만 탐지한다.
def test_다시_찍은_사진이_있으면_마지막_장만_탐지한다(service):
    s = service.start("MB02")
    service.shoot(s.id)  # SLATE
    service.advance(s.id)
    service.shoot(s.id)  # TOP·L1 CAM1·CAM2
    service.retake(s.id)  # TOP·L1을 새 번호로
    image = np.zeros((1080, 1920), dtype=np.uint8)

    result = detect_set(service.store.shots(s.id), SquareDetector(), INFO, load=lambda _p: image)

    assert [(p.face, p.light, p.camera) for p in result.photos] == [("TOP", "L1", "CAM1"), ("TOP", "L1", "CAM2")]


# 테스트 도구: 출고 세트를 가짜 서버에 올려 둔 세션을 만든다.
def _submitted(service, tmp_path, monkeypatch):
    """출고 세트를 가짜 서버에 올려 둔 세션과 그 서버·클라이언트."""
    from fake_capture import BASE, SECRET, FakeCapture

    from trace_booth.capture_client import BoothAuth, CaptureClient
    from trace_booth.checkout import submit_session

    monkeypatch.setenv("TRACE_BOOTH_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("TRACE_API_URL", raising=False)
    server = FakeCapture()
    client = CaptureClient(BASE, BoothAuth(BASE, "booth-out", SECRET, http=server), http=server)
    s = service.start("MB01", "OUT", "Ws0HLv6QRbDILPWOcz5I2", "IPv6mcQOlTkKj6mOecAQt")
    run_to_end(service, s.id, skip_scr1="화면이 켜지지 않음")
    service.complete(s.id)
    record = submit_session(service.store, s.id, client, lambda: "2026-11-02T10:00:00+09:00", http=server,
                            sleep=lambda _: None)
    assert record.status == "SUBMITTED"
    return s, server, client, record.capture_set_id


# [상황] 출고 세트를 올린 뒤 detect를 실행한다.
# [기대] 28장의 탐지가 면·조명·카메라와 원본 좌표로 서버에 올라간다.
def test_detect_명령은_올라간_출고_세트에_탐지를_원본_px와_카메라로_올린다(service, tmp_path, monkeypatch):
    from trace_booth.__main__ import detect

    s, server, client, set_id = _submitted(service, tmp_path, monkeypatch)
    image = np.zeros((1080, 1920), dtype=np.uint8)
    image[100:120, 100:130] = 255

    code = detect(s.public_id, str(_engine(tmp_path)), make_detector=lambda _: SquareDetector(),
                  load=lambda _: image, client=client)

    assert code == 0
    sent = server.detections[set_id]
    photos = {(p["face"], p["light"], p["camera"]) for p in server.sets[set_id]["request"]["photos"]}
    assert len(sent) == 28 and {(d["face"], d["light"], d["camera"]) for d in sent} == photos
    assert sent[0]["class"] == "scratch" and sent[0]["polygon"][0] == [100.0, 100.0]


# [상황] detect를 두 번, 다른 결과로 돌린다.
# [기대] 서버의 부스 탐지는 쌓이지 않고 두 번째 결과로 통째로 바뀐다.
def test_detect를_다시_돌리면_다시_보내고_서버의_부스_탐지는_마지막_결과로_바뀐다(service, tmp_path, monkeypatch):
    from trace_booth.__main__ import detect

    s, server, client, set_id = _submitted(service, tmp_path, monkeypatch)
    one = np.zeros((1080, 1920), dtype=np.uint8)
    one[100:120, 100:130] = 255
    two = np.zeros((1080, 1920), dtype=np.uint8)
    two[300:320, 300:330] = 255
    engine = str(_engine(tmp_path))
    run = lambda image: detect(s.public_id, engine,
                               make_detector=lambda _: SquareDetector(), load=lambda _: image, client=client)

    assert run(one) == 0 and server.detections[set_id][0]["polygon"][0] == [100.0, 100.0]
    assert run(two) == 0
    sent = server.detections[set_id]
    assert len(sent) == 28 and all(d["polygon"][0] == [300.0, 300.0] for d in sent)  # 쌓이지 않고 두 번째 결과로 바뀜


# [상황] 관리자가 이미 등급을 매긴 세트에 detect를 실행한다.
# [기대] 서버엔 못 올리지만 부스에 JSON은 남기고 종료 코드 3으로 알린다.
def test_등급이_매겨진_세트면_탐지를_올리지_못해도_로컬_JSON을_남기고_종료_코드_3을_준다(service, tmp_path, monkeypatch):

    from trace_booth.__main__ import detect

    s, server, client, set_id = _submitted(service, tmp_path, monkeypatch)
    server.sets[set_id]["graded"] = True
    image = np.zeros((1080, 1920), dtype=np.uint8)

    code = detect(s.public_id, str(_engine(tmp_path)), make_detector=lambda _: SquareDetector(),
                  load=lambda _: image, client=client)

    assert code == 3
    assert list((tmp_path / "MB01" / "detections").glob(f"{s.public_id}_v001.json"))
    assert set_id not in server.detections


# [상황] 서버에 올리지 않은(학습용) 세션을 detect한다.
# [기대] 서버를 부르지 않고 부스에만 결과를 남긴다.
def test_detect_명령은_올린_세트가_없으면_서버를_부르지_않고_로컬에만_남긴다(service, tmp_path, monkeypatch):
    from fake_capture import BASE, SECRET, FakeCapture

    from trace_booth.__main__ import detect
    from trace_booth.capture_client import BoothAuth, CaptureClient

    monkeypatch.setenv("TRACE_BOOTH_DATA_DIR", str(tmp_path))
    server = FakeCapture()
    client = CaptureClient(BASE, BoothAuth(BASE, "booth-out", SECRET, http=server), http=server)
    s = service.start("MB01")
    run_to_end(service, s.id, skip_scr1="화면이 켜지지 않음")

    code = detect(s.public_id, str(_engine(tmp_path)), make_detector=lambda _: SquareDetector(),
                  load=lambda _: np.zeros((1080, 1920), dtype=np.uint8), client=client)

    assert code == 0 and server.calls == []
