"""테스트 도구(테스트 자체는 아님): 부스 테스트가 함께 쓰는 준비물.
  - cams: 가짜 카메라 2대(CAM1 위쪽, CAM2), presets: 조명별 카메라 설정(노출·게인 등)
  - service: 임시 폴더에 기록하는 촬영 서비스, run_to_end: 촬영 목록 끝까지 진행하기
"""

from __future__ import annotations

import pytest
from fakes import FakeCamera

from trace_booth.camera_presets import Presets
from trace_booth.codes import Camera
from trace_booth.session import CaptureService
from trace_booth.store import Store


@pytest.fixture
def cams() -> dict[Camera, FakeCamera]:
    """가짜 웹캠 2대(CAM1·CAM2). 찍을 때마다 다른 JPEG를 주고, fail=True로 고장을 흉내 낸다."""
    return {Camera.CAM1: FakeCamera(Camera.CAM1), Camera.CAM2: FakeCamera(Camera.CAM2)}


@pytest.fixture
def presets() -> Presets:
    """조명(L1~L4, 꺼짐)마다 다른 카메라 설정(노출값 100~104). 사진마다 맞는 설정이 들어갔는지 구분하려고 값을 다르게 둔다."""
    values = {light: {"exposure_time_absolute": 100 + i} for i, light in enumerate(["L1", "L2", "L3", "L4", "OFF"])}
    return Presets(values, "test")


@pytest.fixture
def service(tmp_path, cams, presets) -> CaptureService:
    """촬영 서비스. 기록(SQLite)과 사진은 테스트마다 새로 만드는 임시 폴더에 남고, 디스크 여유 검사는 끈다(0바이트)."""
    return CaptureService(Store(tmp_path / "booth.db"), cams, presets, tmp_path / "data", min_free_bytes=0)


def run_to_end(service: CaptureService, session_id: str, skip_scr1: str | None = None) -> None:
    """목록 끝까지 진행한다. skip_scr1을 주면 SCR1을 사유와 함께 건너뛴다."""
    while True:
        step = service.view(session_id).step
        if step is None:
            return
        if step.optional and skip_scr1:
            service.skip_optional(session_id, skip_scr1)
        else:
            if step.captures:
                service.shoot(session_id)
            service.advance(session_id)
