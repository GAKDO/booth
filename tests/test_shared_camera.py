"""카메라를 열어 둔 채 실시간 보기와 촬영이 같이 쓰기(11/11)."""

from __future__ import annotations

import sys
import threading
import time
import types
from collections.abc import Iterator

import pytest

from trace_booth import shared_camera
from trace_booth.__main__ import _keep_open
from trace_booth.camera import CameraError
from trace_booth.codes import Camera
from trace_booth.shared_camera import SharedCamera


class _Frame:
    def __init__(self, seq: int) -> None:
        self.seq = seq
        self.shape = (720, 1280, 3)


class _FakeCapture:
    opened = True
    open_count = 0
    release_count = 0
    fail_after: int | None = None  # 이 장 수만큼 받은 뒤 한 번 끊긴다

    def __init__(self, device: str, backend: int) -> None:
        cls = type(self)
        cls.open_count += 1
        self.seq = 0

    def isOpened(self) -> bool:
        return type(self).opened

    def set(self, prop: int, value: object) -> bool:
        return True

    def grab(self) -> bool:
        time.sleep(0.002)
        cls = type(self)
        if cls.fail_after is not None and self.seq >= cls.fail_after:
            cls.fail_after = None
            return False
        self.seq += 1
        return True

    def retrieve(self) -> tuple[bool, _Frame]:
        return True, _Frame(self.seq)

    def release(self) -> None:
        type(self).release_count += 1


@pytest.fixture
def fake_cv2(monkeypatch: pytest.MonkeyPatch) -> type[_FakeCapture]:
    cls = type("Cap", (_FakeCapture,), {"opened": True, "open_count": 0, "release_count": 0, "fail_after": None})
    mod = types.SimpleNamespace(
        VideoCapture=cls, CAP_V4L2=200, CAP_PROP_FOURCC=6, CAP_PROP_FRAME_WIDTH=3, CAP_PROP_FRAME_HEIGHT=4,
        VideoWriter_fourcc=lambda *a: 0, IMWRITE_JPEG_QUALITY=1, INTER_AREA=3,
        imencode=lambda ext, frame, params: (True, types.SimpleNamespace(tobytes=lambda: str(frame.seq).encode())),
        resize=lambda frame, size, interpolation=0: frame,
    )
    monkeypatch.setitem(sys.modules, "cv2", mod)
    return cls


@pytest.fixture
def started() -> Iterator[list[SharedCamera]]:
    cams: list[SharedCamera] = []
    yield cams
    for cam in cams:
        cam.stop()


def _camera(started: list[SharedCamera], set_controls=lambda d, s: None, warmup: int = 4) -> SharedCamera:
    cam = SharedCamera(Camera.CAM1, "/dev/fake", warmup_frames=warmup, set_controls=set_controls,
                       reopen_delay=0.01)
    started.append(cam)
    cam.start()
    return cam


def test_촬영은_설정을_넣은_뒤_버리는_프레임_다음의_새_프레임을_쓴다(fake_cv2, started) -> None:
    at_set: list[int] = []
    cam = _camera(started, warmup=4)
    cam._set_controls = lambda device, settings: at_set.append(cam._count)

    jpg = cam.capture({"exposure_time_absolute": 2047})

    assert len(at_set) == 1
    assert int(jpg) >= at_set[0] + 5  # 설정 뒤 4장을 버리고 그다음 장
    assert fake_cv2.release_count == 0  # 찍고 나서 닫지 않는다


def test_촬영_중에도_실시간_보기가_멈추지_않는다(fake_cv2, started) -> None:
    go = threading.Event()
    cam = _camera(started, set_controls=lambda d, s: go.wait(5))
    shot = threading.Thread(target=cam.capture, args=({},))
    shot.start()
    try:
        frames = cam.frames(fps=200, sleep=time.sleep)
        got = [next(frames) for _ in range(3)]
        assert all(got)
        assert shot.is_alive()  # 촬영은 아직 설정 중
    finally:
        go.set()
        shot.join(5)
    assert fake_cv2.open_count == 1  # 장치는 한 번만 열었다


def test_프레임이_끊기면_닫고_다시_연다(fake_cv2, started) -> None:
    fake_cv2.fail_after = 3
    cam = _camera(started)
    deadline = time.monotonic() + 5
    while fake_cv2.open_count < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert fake_cv2.open_count >= 2
    assert fake_cv2.release_count >= 1
    assert cam.capture({})  # 다시 열린 뒤 찍힌다


def test_장치를_열지_못하면_촬영은_실패로_돌려준다(fake_cv2, started, monkeypatch) -> None:
    fake_cv2.opened = False
    monkeypatch.setattr(shared_camera, "OPEN_WAIT", 0.1)
    cam = _camera(started)
    with pytest.raises(CameraError, match="장치를 열 수 없음"):
        cam.capture({})
    assert cam.live_jpeg() is None


def test_실시간_보기는_더_새_연결이_오면_끝난다(fake_cv2, started) -> None:
    cam = _camera(started)
    alive = [True]
    frames = cam.frames(current=lambda: alive[0], fps=200, sleep=time.sleep)
    assert next(frames)
    alive[0] = False
    assert list(frames) == []


def test_TRACE_BOOTH_KEEP_OPEN은_기본_1이고_0이면_끈다() -> None:
    assert _keep_open("") is True
    assert _keep_open("1") is True
    assert _keep_open("0") is False
    with pytest.raises(SystemExit):
        _keep_open("yes")
