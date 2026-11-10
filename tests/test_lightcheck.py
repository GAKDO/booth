"""새 부스 밝기·빛샘 확인(11/10). 카메라 없이 가짜 사진과 가짜 촬영으로 확인한다."""

from __future__ import annotations

import json

import numpy as np
import pytest

from trace_booth.__main__ import light_check, main
from trace_booth.codes import Camera, Light
from trace_booth.lightcheck import LightStats, judge_light, measure_light, summarize


def _photo(center: int, background: int = 10, clipped: int = 0) -> np.ndarray:
    """1920×1080 BGR. 가운데 40% 칸만 center 밝기, 왼쪽 위 clipped 픽셀은 255."""
    img = np.full((1080, 1920, 3), background, np.uint8)
    img[324:756, 576:1344] = center
    if clipped:
        img.reshape(-1, 3)[:clipped] = 255
    return img


def test_가운데_칸의_밝기와_날린_픽셀_비율을_잰다() -> None:
    stats = measure_light(_photo(40, clipped=207))  # 207 / 2,073,600 ≈ 0.00998%
    assert stats.center == pytest.approx(40, abs=0.01)
    assert stats.clipped_pct == pytest.approx(0.00998, abs=1e-4)


def test_11월5일_범위_안이면_통과하고_밖이면_어느_쪽으로_옮길지_알려_준다() -> None:
    assert judge_light(Light.L1, LightStats(38, 0.0))[0]
    ok, text = judge_light(Light.L1, LightStats(30, 0.0))
    assert not ok and "램프 쪽으로" in text
    ok, text = judge_light(Light.L3, LightStats(110, 0.0))
    assert not ok and "멀리" in text
    ok, text = judge_light(Light.L2, LightStats(60, 0.5))
    assert not ok and "날림" in text


def test_기준이_없는_조명은_숫자만_보여_주고_통과() -> None:
    ok, text = judge_light(Light.L4, LightStats(80, 0.0))
    assert ok and "기준 없음" in text


def test_11월5일_사진으로_잰_기준을_주면_그_범위로_판정한다() -> None:
    assert not judge_light(Light.L1, LightStats(31, 0.0))[0]
    assert judge_light(Light.L1, LightStats(31, 0.0), center_range=(29, 35))[0]


def test_빛샘은_가운데_5_이하면_통과() -> None:
    assert judge_light(None, LightStats(3.5, 0.0), leak=True)[0]
    ok, text = judge_light(None, LightStats(9.0, 0.0), leak=True)
    assert not ok and "틈" in text


def test_묶음별_중앙값() -> None:
    rows = [("TOP L1 CAM1", LightStats(30, 0.0)), ("TOP L1 CAM1", LightStats(40, 0.02)), ("TOP L1 CAM1", LightStats(36, 0.0))]
    assert summarize(rows) == {"TOP L1 CAM1": (3, 36, 0.0)}


def test_사진_파일은_이름의_조명으로_판정하고_하나라도_밖이면_1(tmp_path, capsys) -> None:
    images = {"MB95_TOP_L1_CAM1_01.jpg": _photo(38), "MB95_TOP_L3_CAM2_01.jpg": _photo(60)}
    rc = light_check(None, False, list(images), ["CAM1"], load=images.__getitem__)
    out = capsys.readouterr().out
    assert rc == 1
    assert "TOP L1 CAM1\t1장\t가운데 38.0" in out
    assert "어두움(기준 98~105)" in out


def test_위_카메라로_찍어_저장하고_판정한다(tmp_path, monkeypatch, capsys) -> None:
    import cv2

    presets = tmp_path / "presets.json"
    presets.write_text(json.dumps({"L1": {"exposure_time_absolute": 1250}, "L2": {"exposure_time_absolute": 2047}}))
    monkeypatch.setenv("TRACE_BOOTH_PRESETS", str(presets))
    monkeypatch.setenv("TRACE_BOOTH_DATA_DIR", str(tmp_path / "data"))
    seen: list[tuple[Camera, dict[str, int]]] = []

    def capture(code: Camera, controls: dict[str, int]) -> bytes:
        seen.append((code, controls))
        return cv2.imencode(".png", _photo(3))[1].tobytes()  # 무손실로 밝기를 그대로

    rc = light_check(None, True, [], ["CAM1", "CAM2"], capture=capture)
    assert rc == 0
    assert seen == [(Camera.CAM1, {"exposure_time_absolute": 2047}), (Camera.CAM2, {"exposure_time_absolute": 2047})]
    assert len(list((tmp_path / "data" / "lightcheck").glob("*_LEAK_CAM*.jpg"))) == 2
    assert "통과" in capsys.readouterr().out


def test_찍을_때는_조명이나_빛샘을_골라야_한다(monkeypatch) -> None:
    with pytest.raises(SystemExit):
        main(["light-check"])


def test_이름에서_조명을_못_읽은_사진은_통과로_치지_않는다() -> None:
    with pytest.raises(SystemExit):
        light_check(None, False, ["photo.jpg"], ["CAM1"], load=lambda _p: _photo(38))


def test_프리셋에_그_조명이_없으면_안내하고_멈춘다(tmp_path, monkeypatch) -> None:
    presets = tmp_path / "presets.json"
    presets.write_text(json.dumps({"L1": {"exposure_time_absolute": 1250}}))
    monkeypatch.setenv("TRACE_BOOTH_PRESETS", str(presets))
    with pytest.raises(SystemExit, match="프리셋"):
        light_check("L4", False, [], ["CAM1"], capture=lambda _c, _s: b"")


def test_새_부스_안내는_면을_찍을_때마다_앞문_닫기를_말한다() -> None:
    from trace_booth.codes import Face
    from trace_booth.shotlist import DOOR, QUICK_SHOTLIST, SHOTLISTS, StepKind, full_inspection_shotlist

    lists = [QUICK_SHOTLIST, full_inspection_shotlist(True), full_inspection_shotlist(False), *SHOTLISTS.values()]
    for steps in lists:
        shots = [s for s in steps if s.kind is StepKind.SHOT and s.face is not Face.SCR1]
        assert shots and all(DOOR in s.instruction for s in shots)


def test_고른_카메라의_장치_이름만_읽는다(tmp_path, monkeypatch) -> None:
    presets = tmp_path / "presets.json"
    presets.write_text(json.dumps({"L1": {"exposure_time_absolute": 1250}}))
    monkeypatch.setenv("TRACE_BOOTH_PRESETS", str(presets))
    monkeypatch.setenv("TRACE_BOOTH_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TRACE_BOOTH_CAM1", "/dev/video-test")
    monkeypatch.delenv("TRACE_BOOTH_CAM2", raising=False)
    import trace_booth.__main__ as m

    opened: list[str] = []

    class FakeCamera:
        def __init__(self, code, device, **_kw) -> None:
            opened.append(device)

        def capture(self, _settings):
            import cv2

            return cv2.imencode(".png", _photo(38))[1].tobytes()

    monkeypatch.setattr(m, "OpenCvCamera", FakeCamera)
    assert light_check("L1", False, [], ["CAM1"]) == 0
    assert opened == ["/dev/video-test"]
