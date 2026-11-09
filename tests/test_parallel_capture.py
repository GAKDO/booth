"""CAM1·CAM2 동시 촬영(11/9): 두 대를 같이 찍고, 한 대라도 실패하면 아무것도 남기지 않는다."""

from __future__ import annotations

import threading
import time

import pytest
from fakes import FakeCamera

from trace_booth.__main__ import _parallel_capture, _warmup_frames
from trace_booth.camera import CameraError
from trace_booth.codes import Camera
from trace_booth.session import CaptureService
from trace_booth.store import Store


class SlowCamera(FakeCamera):
    """찍는 데 0.3초 걸리고, 두 대가 동시에 찍는 중인지 센다."""

    active = 0
    peak = 0
    lock = threading.Lock()

    def capture(self, settings: dict[str, int]) -> bytes:
        with SlowCamera.lock:
            SlowCamera.active += 1
            SlowCamera.peak = max(SlowCamera.peak, SlowCamera.active)
        try:
            time.sleep(0.3)
            return super().capture(settings)
        finally:
            with SlowCamera.lock:
                SlowCamera.active -= 1


def _service(tmp_path, presets, parallel: bool) -> tuple[CaptureService, dict[Camera, SlowCamera]]:
    SlowCamera.active = SlowCamera.peak = 0
    cams = {Camera.CAM1: SlowCamera(Camera.CAM1), Camera.CAM2: SlowCamera(Camera.CAM2)}
    service = CaptureService(Store(tmp_path / "booth.db"), cams, presets, tmp_path / "data", min_free_bytes=0,
                             parallel_capture=parallel)
    return service, cams


def _to_two_camera_step(service: CaptureService, sid: str) -> None:
    while len(service.view(sid).step.cameras) < 2:
        service.shoot(sid)
        service.advance(sid)


@pytest.mark.parametrize("parallel, peak", [(True, 2), (False, 1)])
def test_동시_촬영을_켜면_두_카메라를_같이_찍고_끄면_한_대씩_찍는다(tmp_path, presets, parallel, peak) -> None:
    service, _ = _service(tmp_path, presets, parallel)
    sid = service.start("MB01").id
    _to_two_camera_step(service, sid)
    SlowCamera.peak = 0
    started = time.monotonic()
    shots = service.shoot(sid)
    took = time.monotonic() - started
    assert [s.camera for s in shots] == ["CAM1", "CAM2"]  # 저장 순서는 그대로
    assert SlowCamera.peak == peak
    assert (took < 0.55) if parallel else (took >= 0.6)


def test_동시_촬영에서_한_대가_실패하면_다른_카메라_사진도_남기지_않는다(tmp_path, presets) -> None:
    service, cams = _service(tmp_path, presets, True)
    sid = service.start("MB01").id
    _to_two_camera_step(service, sid)
    before = len(list((tmp_path / "data").rglob("*.jpg")))
    cams[Camera.CAM1].fail = True
    with pytest.raises(CameraError) as e:
        service.shoot(sid)
    assert e.value.camera is Camera.CAM1
    assert len(list((tmp_path / "data").rglob("*.jpg"))) == before


def test_버리는_프레임과_동시_촬영은_환경_변수로_정한다() -> None:
    assert _warmup_frames("") == 4 and _warmup_frames("10") == 10
    assert _parallel_capture("") is True and _parallel_capture("0") is False
    for bad in ("-1", "x", "31"):
        with pytest.raises(SystemExit):
            _warmup_frames(bad)
    with pytest.raises(SystemExit):
        _parallel_capture("yes")
