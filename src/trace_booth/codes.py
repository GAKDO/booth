"""contracts codes.yaml 0.3.0의 촬영 코드 값. 이 파일 밖에 문자열로 흩뿌리지 않는다."""

from enum import Enum

CODES_VERSION = "0.3.0"


class Face(str, Enum):
    TOP = "TOP"
    BOT = "BOT"
    SF = "SF"
    SB = "SB"
    SL = "SL"
    SR = "SR"
    KB = "KB"
    SCR0 = "SCR0"
    SCR1 = "SCR1"


class Light(str, Enum):
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"
    L4 = "L4"
    ROOM = "ROOM"
    OFF = "OFF"


class Camera(str, Enum):
    CAM1 = "CAM1"
    CAM2 = "CAM2"
    PHONE = "PHONE"


# capture.face_required.BOOTH: 세트 완료에 꼭 있어야 하는 면. SCR1은 선택(건너뛰면 사유)
FACE_REQUIRED_BOOTH = (Face.TOP, Face.BOT, Face.SF, Face.SB, Face.SL, Face.SR, Face.KB, Face.SCR0)

# inspection.defect_class: 흠집 4종(긁힘·찍힘·균열·오염). 모델 클래스 이름은 이것과 같아야 한다
DEFECT_CLASSES = ("scratch", "dent", "crack", "stain")
