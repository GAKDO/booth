"""이 파일이 확인하는 것: 부스 Jetson의 흠집 탐지(BO2). YOLO11-seg TensorRT 엔진 출력 풀기, 엔진 만들기 명령,
버튼부터 판정까지 시간 재기. GPU·TensorRT 없이 가짜 출력과 가짜 탐지기로 확인한다.
흠집 종류: scratch 긁힘, dent 찌그러짐, crack 갈라짐, stain 오염(오염은 흠집으로 등급을 깎지 않음).

BO2 Jetson 탐지: YOLO11-seg 출력 풀기, 엔진 빌드 명령, 시간 재기. GPU·TensorRT 없이 가짜 출력과 가짜 탐지기로.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from trace_booth.detect.build import build_engine
from trace_booth.detect.engine import DetectError, ModelInfo, RawDetection
from trace_booth.detect.runner import Photo, detect_photos
from trace_booth.detect.timing import Timeline, grade
from trace_booth.detect.yolo_seg import decode, letterbox

NC = 4


# 테스트 도구: (중심·크기·종류·점수) 목록으로 가짜 엔진 출력을 만든다.
def _outputs(rows: list[tuple[float, float, float, float, int, float]]) -> tuple[np.ndarray, np.ndarray]:
    """(cx, cy, w, h, 클래스, 점수) 줄로 가짜 output0, 계수 0번만 켠 마스크 원형 output1."""
    out0 = np.zeros((1, 4 + NC + 32, 8400), dtype=np.float32)
    for i, (cx, cy, w, h, c, score) in enumerate(rows):
        out0[0, :4, i] = (cx, cy, w, h)
        out0[0, 4 + c, i] = score
        out0[0, 4 + NC, i] = 10.0  # 원형 0번 계수 → sigmoid ≈ 1
    out1 = np.zeros((1, 32, 160, 160), dtype=np.float32)
    out1[0, 0] = 1.0
    return out0, out1


# [상황] 세로로 긴 조각을 엔진 입력(640×640)으로 바꾼다.
# [기대] 비율을 지켜 넣고 양옆 빈 곳은 회색(114)으로 채운다.
def test_세로로_긴_타일은_비율을_지켜_640에_넣고_양옆을_회색으로_채운다():
    tile = np.zeros((640, 320, 3), dtype=np.uint8)
    blob, box = letterbox(tile)
    assert blob.shape == (1, 3, 640, 640) and blob.dtype == np.float32
    assert (box.scale, box.pad_x, box.pad_y) == (1.0, 160.0, 0.0)
    assert blob[0, 0, 0, 0] == pytest.approx(114 / 255) and blob[0, 0, 0, 320] == 0.0


# [상황] 같은 종류로 거의 겹친 두 결과, 다른 종류 하나, 신뢰도 낮은 하나가 나왔다.
# [기대] 신뢰도 낮은 것은 버리고, 같은 종류 겹침은 하나만 남기며(다른 종류는 남김), 다각형을 조각 좌표로 되돌린다.
def test_엔진_출력은_신뢰도로_거르고_같은_클래스만_겹침을_지우고_폴리곤을_타일_px로_되돌린다():
    _, box = letterbox(np.zeros((640, 320, 3), dtype=np.uint8))
    out0, out1 = _outputs([
        (260, 100, 80, 40, 1, 0.9),   # 입력 220~300 → 타일 x 60~140, y 80~120
        (262, 101, 80, 40, 1, 0.5),   # 같은 클래스, 거의 같은 상자 → 지움
        (260, 100, 80, 40, 2, 0.6),   # 다른 클래스는 남김
        (500, 500, 30, 30, 0, 0.1),   # 신뢰도 낮음
    ])

    found = decode(out0, out1, box, min_confidence=0.25)

    assert [(f.class_index, round(f.confidence, 2)) for f in found] == [(1, 0.9), (2, 0.6)]
    xs = [p[0] for p in found[0].polygon]
    ys = [p[1] for p in found[0].polygon]
    assert len(found[0].polygon) >= 3
    assert min(xs) == pytest.approx(60, abs=4) and max(xs) == pytest.approx(140, abs=4)
    assert min(ys) == pytest.approx(80, abs=4) and max(ys) == pytest.approx(120, abs=4)


# [상황] 엔진이 아무것도 잡지 않았다.
# [기대] 빈 목록.
def test_잡힌_것이_없으면_빈_목록():
    _, box = letterbox(np.zeros((640, 640, 3), dtype=np.uint8))
    assert decode(*_outputs([]), box) == []


# 테스트 도구: 첫 조각에 긁힘 하나를 내는 가짜 탐지기.
class OneScratch:
    def infer(self, tile) -> list[RawDetection]:
        return [RawDetection(0, 0.8, [(1, 1), (20, 1), (20, 5)])] if tile[0, 0] == 255 else []


INFO = ModelInfo("v001", ("scratch", "dent", "crack", "stain"))


# [상황] 사진 4장을 탐지한다.
# [기대] 사진마다 면·카메라와 원본 좌표 다각형이 오고, 하나라도 잡히면 "문제 있음", 시간도 기록된다.
def test_사진_4장을_넘기면_사진마다_면_카메라와_원본_px_폴리곤이_오고_하나라도_잡히면_문제_있음():
    clean = np.zeros((1080, 1920), dtype=np.uint8)
    dirty = clean.copy()
    dirty[0, 0] = 255
    photos = [Photo("BOT", "L1", "CAM1", clean), Photo("BOT", "L1", "CAM2", clean),
              Photo("TOP", "L1", "CAM1", dirty), Photo("TOP", "L1", "CAM2", clean)]
    ticks = iter([0.0, 3.2])

    result = detect_photos(photos, OneScratch(), INFO, clock=lambda: next(ticks))

    assert result.has_problem and result.count == 1 and result.seconds == 3.2
    assert [(p.face, p.camera, len(p.found)) for p in result.photos] == [
        ("BOT", "CAM1", 0), ("BOT", "CAM2", 0), ("TOP", "CAM1", 1), ("TOP", "CAM2", 0)]
    assert result.detections()[0]["polygon"][0] == [1.0, 1.0] and result.to_json()["hasProblem"] is True
    assert not detect_photos(photos[:1], OneScratch(), INFO).has_problem


# [상황] 시험용(testOnly) model.json을 읽는다.
# [기대] 종류 검사를 건너뛰고, 이름 없는 종류는 "class5"처럼 번호로 부른다.
def test_시험용_모델은_클래스_검사를_건너뛰고_이름이_없으면_번호로_부른다(tmp_path):
    path = tmp_path / "model.json"
    path.write_text(json.dumps({"modelVersion": "yolo11n-seg-test", "classes": [], "testOnly": True}))
    info = ModelInfo.load(path)
    assert info.test_only and info.class_name(5) == "class5"


def _fake_trtexec(calls):
    def run(cmd, log):
        calls.append(cmd)
        engine = next(a.split("=", 1)[1] for a in cmd if a.startswith("--saveEngine="))
        Path(engine).write_bytes(b"engine")
        log.write_text("ok")
        return 0
    return run


# [상황] ONNX로 엔진을 만든다.
# [기대] trtexec를 FP16으로 부르고, 검사한 model.json을 엔진 옆에 둔다.
def test_엔진_빌드는_trtexec를_FP16으로_부르고_검사한_model_json을_엔진_옆에_둔다(tmp_path):
    onnx = tmp_path / "best.onnx"
    onnx.write_bytes(b"onnx")
    (tmp_path / "model.json").write_text(json.dumps({"modelVersion": "v001", "classes": list(INFO.classes)}))
    calls: list[list[str]] = []

    engine = build_engine(onnx, tmp_path / "out", trtexec="true", run=_fake_trtexec(calls))

    assert engine == tmp_path / "out" / "best.engine" and engine.exists()
    assert "--fp16" in calls[0] and f"--onnx={onnx}" in calls[0]
    assert ModelInfo.load(engine.parent / "model.json").model_version == "v001"


# [상황] model.json이 없거나 종류가 codes.yaml과 다르다.
# [기대] trtexec를 부르지 않고 안내(--test, codes.yaml)와 함께 멈춘다.
def test_엔진_빌드는_model_json이_없거나_클래스가_다르면_trtexec를_부르지_않는다(tmp_path):
    onnx = tmp_path / "best.onnx"
    onnx.write_bytes(b"onnx")
    calls: list[list[str]] = []
    with pytest.raises(DetectError, match="--test"):
        build_engine(onnx, tmp_path / "out", trtexec="true", run=_fake_trtexec(calls))
    (tmp_path / "model.json").write_text(json.dumps({"modelVersion": "v1", "classes": ["person"]}))
    with pytest.raises(DetectError, match="codes.yaml"):
        build_engine(onnx, tmp_path / "out", trtexec="true", run=_fake_trtexec(calls))
    assert calls == []


# [상황] 시험용 기본 모델로 엔진을 만든다.
# [기대] testOnly 표시가 붙은 model.json으로 만든다.
def test_시험용_기본_모델은_testOnly_model_json으로_엔진을_만든다(tmp_path):
    onnx = tmp_path / "yolo11n-seg.onnx"
    onnx.write_bytes(b"onnx")
    engine = build_engine(onnx, tmp_path / "out", test=True, trtexec="true", run=_fake_trtexec([]))
    info = ModelInfo.load(engine.parent / "model.json")
    assert info.test_only and info.model_version == "yolo11n-seg-test-trt-fp16"


# [상황] trtexec가 실패한다.
# [기대] 로그 위치를 알려 주며 멈춘다.
def test_trtexec가_실패하면_로그_위치와_함께_멈춘다(tmp_path):
    onnx = tmp_path / "best.onnx"
    onnx.write_bytes(b"onnx")
    with pytest.raises(DetectError, match="로그"):
        build_engine(onnx, tmp_path / "out", test=True, trtexec="true", run=lambda cmd, log: 1)


# [상황] "촬영 BOT → 촬영 TOP → 탐지" 구간 시간을 기록한다.
# [기대] 합계와 구간별 시간이 남고, 30초 이하 OK, 30초 초과 SLOW, 60초 초과 OVER(심사 목표 30초·한도 60초).
def test_버튼에서_판정까지_구간별_시간과_목표_기준_판정을_남긴다():
    ticks = iter([100.0, 104.0, 109.5, 112.0])
    t = Timeline(clock=lambda: next(ticks))
    t.mark("촬영 BOT")
    t.mark("촬영 TOP")
    t.mark("탐지")
    out = t.to_json()
    assert out["total"] == 12.0 and out["grade"] == "OK"
    assert [s["seconds"] for s in out["steps"]] == [4.0, 5.5, 2.5]
    assert (grade(30.0), grade(30.1), grade(60.1)) == ("OK", "SLOW", "OVER")


# [상황] bench로 같은 사진을 여러 번 탐지한다. 느린 경우도 흉내 낸다.
# [기대] 중간값으로 판정해 기준 안이면 0, 넘으면 OVER와 종료 코드 2.
def test_bench는_사진을_여러_번_탐지해_중간값으로_판정하고_기준을_넘으면_종료_코드_2(tmp_path, capsys):
    from trace_booth.__main__ import bench

    engine = tmp_path / "best.engine"
    engine.write_bytes(b"fake")
    (tmp_path / "model.json").write_text(json.dumps({"modelVersion": "v001", "classes": list(INFO.classes)}))
    image = np.zeros((1080, 1920), dtype=np.uint8)
    now = [0.0]

    def clock() -> float:
        now[0] += 1.0
        return now[0]

    code = bench(str(engine), ["a.jpg"] * 4, None, 3, make_detector=lambda _: OneScratch(), load=lambda _: image,
                 clock=clock)
    assert code == 0 and "문제 없음" in capsys.readouterr().out

    now[0] = 0.0
    slow = iter([0, 0, 0, 0, 0, 0, 0, 70, 0, 70, 0, 70, 0, 70])
    code = bench(str(engine), ["a.jpg"], None, 3, make_detector=lambda _: OneScratch(), load=lambda _: image,
                 clock=lambda: float(next(slow)))
    assert code == 2 and "OVER" in capsys.readouterr().out


# [상황] 시험용 엔진으로 탐지했고, 서버에 올린 세트가 있다.
# [기대] 탐지 결과를 서버에 보내지 않는다(가짜 결과가 기록에 들어가지 않게).
def test_시험용_모델로_탐지하면_올린_세트가_있어도_서버에_보내지_않는다(service, tmp_path, monkeypatch):
    from test_detect import _submitted

    from trace_booth.__main__ import detect

    s, server, client, set_id = _submitted(service, tmp_path, monkeypatch)
    engine = tmp_path / "m" / "yolo11n-seg.engine"
    engine.parent.mkdir()
    engine.write_bytes(b"fake")
    (engine.parent / "model.json").write_text(json.dumps({"modelVersion": "t", "classes": [], "testOnly": True}))
    image = np.full((1080, 1920), 255, dtype=np.uint8)

    assert detect(s.public_id, str(engine), make_detector=lambda _: OneScratch(), load=lambda _: image,
                  client=client) == 0
    assert set_id not in server.detections


# [상황] 다른 모델의 엔진이 있는 폴더에 엔진을 만들려 한다. 같은 모델이면 다시 만든다.
# [기대] 다른 모델이면 "폴더를 따로" 쓰라며 멈추고, 같은 모델이면 다시 만든다(잘못 덮어쓰기 방지).
def test_다른_엔진이_있는_폴더에는_만들지_않고_같은_엔진은_다시_만든다(tmp_path):
    onnx = tmp_path / "best.onnx"
    onnx.write_bytes(b"onnx")
    out = tmp_path / "out"
    build_engine(onnx, out, test=True, trtexec="true", run=_fake_trtexec([]))
    build_engine(onnx, out, test=True, trtexec="true", run=_fake_trtexec([]))  # 같은 엔진 다시
    other = tmp_path / "yolo11n-seg.onnx"
    other.write_bytes(b"onnx")
    with pytest.raises(DetectError, match="폴더를 따로"):
        build_engine(other, out, test=True, trtexec="true", run=_fake_trtexec([]))


# 테스트 도구: 오염(stain) 하나만 내는 가짜 탐지기.
class OneStain:
    def infer(self, tile) -> list[RawDetection]:
        return [RawDetection(3, 0.8, [(1, 1), (20, 1), (20, 5)])] if tile[0, 0] == 255 else []


# [상황] 오염만 잡혔다.
# [기대] 결과에는 남기되 "문제 없음"(문제 개수 0). 서버 등급 규칙과 같다.
def test_오염만_잡히면_흠집은_남기되_서버_등급처럼_문제_없음이다():
    dirty = np.zeros((1080, 1920), dtype=np.uint8)
    dirty[0, 0] = 255

    result = detect_photos([Photo("TOP", "L1", "CAM1", dirty)], OneStain(), INFO)

    assert result.count == 1 and result.problem_count == 0 and not result.has_problem
    assert result.detections()[0]["class"] == "stain"
    assert result.to_json()["hasProblem"] is False and result.to_json()["problemCount"] == 0


# 테스트 도구: 모델에 없는 종류 번호(9)를 내는 가짜 탐지기.
class OutOfRange:
    def infer(self, tile) -> list[RawDetection]:
        return [RawDetection(9, 0.8, [(1, 1), (20, 1), (20, 5)])] if tile[0, 0] == 255 else []


# [상황] 엔진이 모르는 종류(class9)를 냈다.
# [기대] 정상으로 넘기지 않고 "문제 있음"으로 본다(놓치는 것보다 사람이 확인하게).
def test_엔진이_모르는_클래스를_내면_정상으로_넘기지_않고_문제_있음이다():
    odd = np.zeros((1080, 1920), dtype=np.uint8)
    odd[0, 0] = 255

    result = detect_photos([Photo("TOP", "L1", "CAM1", odd)], OutOfRange(), INFO)

    assert result.detections()[0]["class"] == "class9"
    assert result.problem_count == 1 and result.has_problem and result.to_json()["problemCount"] == 1
