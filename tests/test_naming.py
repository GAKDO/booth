import pytest

from trace_booth.codes import Camera, Face, Light
from trace_booth.naming import FILENAME_RE, is_valid_device_id, parse_filename, shot_filename, slate_filename


@pytest.mark.parametrize("device_id", ["MB01", "REG01", "GRAM02"])
def test_올바른_기기_ID는_통과한다(device_id):
    assert is_valid_device_id(device_id)


@pytest.mark.parametrize("device_id", ["", "mb01", "MB1", "M01", "MACBK01", "MB001", "MB-01", "MB01 "])
def test_잘못된_기기_ID는_거절한다(device_id):
    assert not is_valid_device_id(device_id)
    with pytest.raises(ValueError):
        shot_filename(device_id, Face.TOP, Light.L1, Camera.CAM1, 1)


def test_파일명은_규칙을_따르고_다시_읽힌다():
    name = shot_filename("MB01", Face.TOP, Light.L2, Camera.CAM1, 1)
    assert name == "MB01_TOP_L2_CAM1_01.jpg"
    assert FILENAME_RE.match(name)
    parsed = parse_filename(name)
    assert parsed is not None
    assert (parsed.device_id, parsed.face, parsed.light, parsed.camera, parsed.seq) == (
        "MB01", Face.TOP, Light.L2, Camera.CAM1, 1,
    )


def test_SCR1은_OFF_조명으로_이름을_만든다():
    assert shot_filename("MB01", Face.SCR1, Light.OFF, Camera.CAM1, 2) == "MB01_SCR1_OFF_CAM1_02.jpg"


def test_SLATE는_면_파일명_규칙에_걸리지_않는다():
    name = slate_filename("MB01", 1)
    assert name == "MB01_SLATE_CAM1_01.jpg"
    assert parse_filename(name) is None


@pytest.mark.parametrize("name", ["MB01_TOP_ROOM_CAM1_01.jpg", "MB01_TOP_L1_PHONE_01.jpg", "MB01_TOP_L1_CAM1_1.jpg"])
def test_부스에서_쓰지_않는_값은_파일명으로_인정하지_않는다(name):
    assert parse_filename(name) is None
