"""기기 ID 검사와 사진 파일명 규칙 `{기기ID}_{면}_{조명}_{카메라}_{번호}.jpg`."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .codes import Camera, Face, Light

# 학습 데이터는 ID 카드의 부스 기기 코드(MB01), 빠른 검사는 렌탈 목록에서 고른 rental-server 기기 publicId(21자)
_DEVICE = r"[A-Z]{2,4}[0-9]{2}|[A-Za-z0-9_-]{21}"
DEVICE_ID_RE = re.compile(rf"^(?:{_DEVICE})$")
_BOOTH_LIGHTS = (Light.L1, Light.L2, Light.L3, Light.L4, Light.OFF)
_BOOTH_CAMERAS = (Camera.CAM1, Camera.CAM2)
FILENAME_RE = re.compile(
    rf"^(?P<device>{_DEVICE})"
    rf"_(?P<face>{'|'.join(f.value for f in Face)})"
    rf"_(?P<light>{'|'.join(x.value for x in _BOOTH_LIGHTS)})"
    rf"_(?P<camera>{'|'.join(c.value for c in _BOOTH_CAMERAS)})"
    r"_(?P<seq>[0-9]{2})\.jpg$"
)


@dataclass(frozen=True)
class ParsedName:
    device_id: str
    face: Face
    light: Light
    camera: Camera
    seq: int


def is_valid_device_id(device_id: str) -> bool:
    return bool(DEVICE_ID_RE.match(device_id))


def shot_filename(device_id: str, face: Face, light: Light, camera: Camera, seq: int) -> str:
    if not is_valid_device_id(device_id):
        raise ValueError(f"기기 ID 형식이 아님: {device_id}")
    if not 1 <= seq <= 99:
        raise ValueError(f"번호는 1~99: {seq}")
    return f"{device_id}_{face.value}_{light.value}_{camera.value}_{seq:02d}.jpg"


def slate_filename(device_id: str, seq: int) -> str:
    """SLATE는 면 코드가 아니라 파일명 규칙과 따로 둔다(contracts/storage-layout.md)."""
    if not is_valid_device_id(device_id):
        raise ValueError(f"기기 ID 형식이 아님: {device_id}")
    return f"{device_id}_SLATE_CAM1_{seq:02d}.jpg"


def parse_filename(name: str) -> ParsedName | None:
    m = FILENAME_RE.match(name)
    if not m:
        return None
    return ParsedName(m["device"], Face(m["face"]), Light(m["light"]), Camera(m["camera"]), int(m["seq"]))
