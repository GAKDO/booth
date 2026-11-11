"""닫기를 뒤로 미루는 촬영(11/11): 사진은 바로 돌려주고, 닫는 동안 실시간 보기와 다음 촬영은 기다린다."""

from __future__ import annotations

import sys
import threading
import time
import types

import pytest

from trace_booth.camera import CameraError, OpenCvCamera
from trace_booth.codes import Camera
from trace_booth.live import CameraGate


class _Frame:
    pass


class _FakeCapture:
    opened = True
    release_started = threading.Event()
    release_go = threading.Event()
    open_count = 0

    def __init__(self, device: str, backend: int) -> None:
        type(self).open_count += 1

    def isOpened(self) -> bool:
        return type(self).opened

    def set(self, prop: int, value: object) -> bool:
        return True

    def read(self) -> tuple[bool, _Frame]:
        return True, _Frame()

    def release(self) -> None:
        type(self).release_started.set()
        assert type(self).release_go.wait(5)


@pytest.fixture
def fake_cv2(monkeypatch: pytest.MonkeyPatch) -> type[_FakeCapture]:
    cls = type("Cap", (_FakeCapture,), {
        "opened": True, "release_started": threading.Event(), "release_go": threading.Event(), "open_count": 0,
    })
    mod = types.SimpleNamespace(
        VideoCapture=cls, CAP_V4L2=200, CAP_PROP_FOURCC=6, CAP_PROP_FRAME_WIDTH=3, CAP_PROP_FRAME_HEIGHT=4,
        VideoWriter_fourcc=lambda *a: 0, IMWRITE_JPEG_QUALITY=1,
        imencode=lambda ext, frame, params: (True, types.SimpleNamespace(tobytes=lambda: b"jpg")),
    )
    monkeypatch.setitem(sys.modules, "cv2", mod)
    return cls


def _camera(gate: CameraGate | None) -> OpenCvCamera:
    return OpenCvCamera(Camera.CAM1, "/dev/fake", warmup_frames=0, set_controls=lambda d, s: None, gate=gate)


def test_사진은_닫기를_기다리지_않고_바로_돌려준다(fake_cv2: type[_FakeCapture]) -> None:
    gate = CameraGate()
    assert _camera(gate).capture({}) == b"jpg"
    assert fake_cv2.release_started.wait(2)
    assert gate.capture_pending()  # 닫는 동안 실시간 보기는 열지 않는다
    fake_cv2.release_go.set()
    deadline = time.monotonic() + 2
    while gate.capture_pending() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not gate.capture_pending()


def test_다음_촬영은_앞_닫기가_끝난_뒤에_장치를_연다(fake_cv2: type[_FakeCapture]) -> None:
    cam = _camera(CameraGate())
    cam.capture({})
    assert fake_cv2.release_started.wait(2)
    result: list[bytes] = []
    t = threading.Thread(target=lambda: result.append(cam.capture({})))
    t.start()
    time.sleep(0.2)
    assert fake_cv2.open_count == 1  # 아직 앞 촬영이 닫는 중
    fake_cv2.release_go.set()
    t.join(5)
    assert result == [b"jpg"] and fake_cv2.open_count == 2


def test_장치를_열지_못하면_게이트를_바로_놓는다(fake_cv2: type[_FakeCapture]) -> None:
    fake_cv2.opened = False
    gate = CameraGate()
    with pytest.raises(CameraError):
        _camera(gate).capture({})
    assert not gate.capture_pending()


def test_게이트가_없으면_예전처럼_닫고_돌려준다(fake_cv2: type[_FakeCapture]) -> None:
    fake_cv2.release_go.set()
    assert _camera(None).capture({}) == b"jpg"
    assert fake_cv2.release_started.is_set()


def test_앞_닫기가_제한_시간을_넘기면_촬영을_실패로_돌려준다(
    fake_cv2: type[_FakeCapture], monkeypatch: pytest.MonkeyPatch
) -> None:
    from trace_booth import camera

    monkeypatch.setattr(camera, "CLOSE_WAIT", 0.1)
    cam = _camera(CameraGate())
    cam.capture({})
    assert fake_cv2.release_started.wait(2)
    with pytest.raises(CameraError):
        cam.capture({})
    assert fake_cv2.open_count == 1
    fake_cv2.release_go.set()
