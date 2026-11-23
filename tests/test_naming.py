# 이 파일이 확인하는 것: 부스 사진 파일 이름 규칙. 예) MB01_TOP_L2_CAM1_01.jpg = 기기 MB01, 윗면, 조명 L2, 카메라 1, 1번째.
# 이름만 보고 어떤 기기·면·조명·카메라인지 알 수 있어야 학습 데이터 빌더가 자동으로 분류할 수 있다.

import pytest

from trace_booth.codes import Camera, Face, Light
from trace_booth.naming import FILENAME_RE, is_valid_device_id, parse_filename, shot_filename, slate_filename


# [상황] MB01·REG01·GRAM02 같은 기기 ID.
# [기대] 올바른 ID로 통과한다(대문자 2~4자 + 숫자 2자리).
@pytest.mark.parametrize("device_id", ["MB01", "REG01", "GRAM02"])
def test_올바른_기기_ID는_통과한다(device_id):
    assert is_valid_device_id(device_id)


# [상황] 빈 값, 소문자, 숫자 1자리·3자리, 하이픈·공백이 들어간 ID.
# [기대] 거절한다.
@pytest.mark.parametrize("device_id", ["", "mb01", "MB1", "M01", "MACBK01", "MB001", "MB-01", "MB01 "])
def test_잘못된_기기_ID는_거절한다(device_id):
    assert not is_valid_device_id(device_id)
    with pytest.raises(ValueError):
        shot_filename(device_id, Face.TOP, Light.L1, Camera.CAM1, 1)


# [상황] 기기·면·조명·카메라·번호로 파일 이름을 만들고 다시 해석한다.
# [기대] MB01_TOP_L2_CAM1_01.jpg가 되고, 해석하면 원래 값이 그대로 나온다.
def test_파일명은_규칙을_따르고_다시_읽힌다():
    name = shot_filename("MB01", Face.TOP, Light.L2, Camera.CAM1, 1)
    assert name == "MB01_TOP_L2_CAM1_01.jpg"
    assert FILENAME_RE.match(name)
    parsed = parse_filename(name)
    assert parsed is not None
    assert (parsed.device_id, parsed.face, parsed.light, parsed.camera, parsed.seq) == (
        "MB01", Face.TOP, Light.L2, Camera.CAM1, 1,
    )


# [상황] 켜진 흰 화면(SCR1) 사진 이름을 만든다(조명을 끄고 찍음).
# [기대] 조명 칸이 OFF다.
def test_SCR1은_OFF_조명으로_이름을_만든다():
    assert shot_filename("MB01", Face.SCR1, Light.OFF, Camera.CAM1, 2) == "MB01_SCR1_OFF_CAM1_02.jpg"


# [상황] 기기 ID 판(SLATE) 사진 이름을 만든다.
# [기대] MB01_SLATE_CAM1_01.jpg이고, 면 사진으로 해석되지 않는다(학습 데이터에 섞이지 않게).
def test_SLATE는_면_파일명_규칙에_걸리지_않는다():
    name = slate_filename("MB01", 1)
    assert name == "MB01_SLATE_CAM1_01.jpg"
    assert parse_filename(name) is None


# [상황] 부스에서 쓰지 않는 값(ROOM 조명, PHONE 카메라, 번호 1자리)이 든 이름.
# [기대] 부스 사진 이름으로 인정하지 않는다.
@pytest.mark.parametrize("name", ["MB01_TOP_ROOM_CAM1_01.jpg", "MB01_TOP_L1_PHONE_01.jpg", "MB01_TOP_L1_CAM1_1.jpg"])
def test_부스에서_쓰지_않는_값은_파일명으로_인정하지_않는다(name):
    assert parse_filename(name) is None
