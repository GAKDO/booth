from __future__ import annotations

import pytest
from fakes import FakeCamera

from trace_booth.camera_presets import Presets
from trace_booth.codes import Camera
from trace_booth.session import CaptureService
from trace_booth.store import Store


@pytest.fixture
def cams() -> dict[Camera, FakeCamera]:
    return {Camera.CAM1: FakeCamera(Camera.CAM1), Camera.CAM2: FakeCamera(Camera.CAM2)}


@pytest.fixture
def presets() -> Presets:
    values = {light: {"exposure_time_absolute": 100 + i} for i, light in enumerate(["L1", "L2", "L3", "L4", "OFF"])}
    return Presets(values, "test")


@pytest.fixture
def service(tmp_path, cams, presets) -> CaptureService:
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
