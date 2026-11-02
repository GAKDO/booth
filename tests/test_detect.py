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


def test_1920x1080_사진을_타일로_자르면_전체를_빠짐없이_덮고_이웃_타일이_겹친다():
    ts = tiles(1920, 1080, size=640, overlap=0.2)
    covered = np.zeros((1080, 1920), dtype=bool)
    for t in ts:
        covered[t.y:t.y + t.h, t.x:t.x + t.w] = True
    assert covered.all()
    xs = sorted({t.x for t in ts})
    assert all(b - a <= 640 - 128 for a, b in itertools.pairwise(xs))  # 20% 이상 겹침
    assert all(t.w == 640 and t.h == 640 for t in ts)


def test_타일_안의_폴리곤을_원본_좌표로_되돌리면_사진_안에_있다():
    poly = to_image([(10, 20), (700, 20), (700, 50)], Tile(1280, 440, 640, 640), 1920, 1080)
    assert poly[0] == [1290.0, 460.0]
    assert all(0 <= x <= 1920 and 0 <= y <= 1080 for x, y in poly)


def test_겹치는_두_타일이_같은_흠집을_잡으면_신뢰도가_높은_하나만_남는다():
    a = Found("scratch", [[100, 100], [140, 100], [140, 120], [100, 120]], 0.6)
    b = Found("scratch", [[102, 101], [141, 101], [141, 121], [102, 121]], 0.9)
    c = Found("dent", [[100, 100], [140, 100], [140, 120], [100, 120]], 0.5)  # 다른 클래스는 남김
    assert dedupe([a, b, c]) == [b, c]


def test_타일_경계에_걸친_흠집도_원본에서는_하나로_잡힌다():
    image = np.zeros((1080, 1920), dtype=np.uint8)
    image[300:320, 500:540] = 255  # 첫째·둘째 타일이 겹치는 x 512 부근
    found = detect_photo(image, SquareDetector(), INFO)
    assert len(found) == 1
    assert found[0].defect_class == "scratch"
    x0, y0, x1, y1 = found[0].box
    assert (x0, y0, x1, y1) == (500.0, 300.0, 539.0, 319.0)


def test_타일_끝에_조금만_걸려_잘린_조각은_따로_세지_않는다():
    image = np.zeros((1080, 1920), dtype=np.uint8)
    image[500:520, 630:660] = 255  # 첫째 타일(0~639)에는 10px만, 둘째 타일(512~)에는 전부
    found = detect_photo(image, SquareDetector(), INFO)
    assert len(found) == 1
    assert found[0].box == (630.0, 500.0, 659.0, 519.0)


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


def _engine(tmp_path, classes=("scratch", "dent", "crack", "stain")):
    engine = tmp_path / "models" / "best.engine"
    engine.parent.mkdir()
    engine.write_bytes(b"fake")
    (engine.parent / "model.json").write_text(json.dumps({"modelVersion": "v001", "classes": list(classes)}))
    return engine


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


def test_detect_명령은_모델_클래스가_다르면_시작하지_않는다(service, tmp_path, monkeypatch):
    from trace_booth.__main__ import detect

    monkeypatch.setenv("TRACE_BOOTH_DATA_DIR", str(tmp_path))  # service 픽스처의 booth.db와 같은 곳
    s = service.start("MB01")
    with pytest.raises(SystemExit, match="탐지를 시작할 수 없어요"):
        detect(s.public_id, str(_engine(tmp_path, ("scratch",))), make_detector=lambda _: SquareDetector())
