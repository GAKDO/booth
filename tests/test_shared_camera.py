"""카메라를 열어 둔 채 실시간 보기와 촬영이 같이 쓰기(11/11)."""

from __future__ import annotations

import sys
import threading
import time
import types
from collections.abc import Iterator
from typing import ClassVar

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
    raise_after: int | None = None  # 이 장 수만큼 받은 뒤 한 번 예외를 낸다
    threads: ClassVar[set[str]] = set()  # 장치를 만진 스레드 이름

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
        cls.threads.add(threading.current_thread().name)
        if cls.raise_after is not None and self.seq >= cls.raise_after:
            cls.raise_after = None
            raise RuntimeError("cv2.error 흉내")
        if cls.fail_after is not None and self.seq >= cls.fail_after:
            cls.fail_after = None
            return False
        self.seq += 1
        return True

    def retrieve(self) -> tuple[bool, _Frame]:
        type(self).threads.add(threading.current_thread().name)
        return True, _Frame(self.seq)

    def release(self) -> None:
        type(self).release_count += 1


@pytest.fixture
def fake_cv2(monkeypatch: pytest.MonkeyPatch) -> type[_FakeCapture]:
    cls = type("Cap", (_FakeCapture,), {"opened": True, "open_count": 0, "release_count": 0, "fail_after": None,
                                     "raise_after": None, "threads": set()})
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


def test_읽다가_예외가_나도_닫고_다시_연다(fake_cv2, started) -> None:
    fake_cv2.raise_after = 3
    cam = _camera(started)
    deadline = time.monotonic() + 5
    while fake_cv2.open_count < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert fake_cv2.open_count >= 2
    assert fake_cv2.release_count >= 1
    assert cam.capture({})


def test_장치는_뒤_스레드만_만진다(fake_cv2, started) -> None:
    cam = _camera(started)
    cam.capture({})
    assert cam.live_jpeg()
    assert fake_cv2.threads == {"cam-CAM1"}


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


def test_묶음을_바꾸면_앞_묶음을_닫은_뒤_새_묶음을_열어_찍는다(
    fake_cv2: type[_FakeCapture], started: list[SharedCamera]
) -> None:
    from trace_booth.shared_camera import CameraGroups

    top1, top2, side = (SharedCamera(code, f"/dev/{n}", reopen_delay=0.01, set_controls=lambda d, s: None)
                        for code, n in ((Camera.CAM1, "a"), (Camera.CAM2, "b"), (Camera.CAM1, "side")))
    groups = CameraGroups({"top": [top1, top2], "side": [side]}, active="top")
    for cam in (top1, top2, side):
        started.append(cam)
        cam.start()

    assert top1.capture({}) and top2.capture({})
    assert side._cap is None  # 옆면은 처음엔 닫혀 있다
    opens = fake_cv2.open_count

    assert side.capture({})  # 옆면으로 찍으면 위 두 대를 닫고 옆면을 연다
    assert groups.active == "side"
    assert top1._cap is None and top2._cap is None and not top1._held and not top2._held
    assert fake_cv2.open_count == opens + 1

    assert top1.capture({})  # 다시 위로 돌아오면 옆면을 닫는다
    assert groups.active == "top"
    assert side._cap is None and not side._held


def test_쉬는_카메라의_실시간_보기는_묶음을_바꾸지_않는다(
    fake_cv2: type[_FakeCapture], started: list[SharedCamera]
) -> None:
    from trace_booth.shared_camera import CameraGroups

    top = SharedCamera(Camera.CAM1, "/dev/a", reopen_delay=0.01)
    side = SharedCamera(Camera.CAM1, "/dev/side", reopen_delay=0.01)
    groups = CameraGroups({"top": [top], "side": [side]}, active="top")
    for cam in (top, side):
        started.append(cam)
        cam.start()

    assert side.live_jpeg() is None
    assert groups.active == "top"


def test_쉬는_중에_멈추라고_하면_스레드가_끝난다(fake_cv2: type[_FakeCapture]) -> None:
    cam = SharedCamera(Camera.CAM1, "/dev/a", reopen_delay=0.01, paused=True)
    cam.start()
    cam.stop(2)
    assert cam._thread is not None and not cam._thread.is_alive()


def test_TRACE_BOOTH_SIDE_EXCLUSIVE는_기본_1이고_0이면_끈다() -> None:
    from trace_booth.__main__ import _side_exclusive

    assert _side_exclusive("") is True
    assert _side_exclusive("0") is False
    with pytest.raises(SystemExit):
        _side_exclusive("2")


def test_앞_묶음이_제때_닫히지_않으면_새_묶음을_열지_않고_찍기를_실패로_돌려준다(
    fake_cv2: type[_FakeCapture], started: list[SharedCamera]
) -> None:
    from trace_booth.shared_camera import CameraGroups

    top = SharedCamera(Camera.CAM1, "/dev/a", reopen_delay=0.01)
    side = SharedCamera(Camera.CAM1, "/dev/side", reopen_delay=0.01)
    groups = CameraGroups({"top": [top], "side": [side]}, active="top")
    for cam in (top, side):
        started.append(cam)
        cam.start()
    assert top._wait_open(5) is not None

    assert groups.use("side", timeout=0) is False  # 닫기를 기다리지 않으면 아직 열려 있다
    assert groups.active == "top"
    assert side._paused  # 옆면은 열지 않았다
    assert top.capture({})  # 위 카메라는 되살아나 그대로 찍힌다

    side.before_use = lambda: False
    with pytest.raises(CameraError, match="닫히지 않아"):
        side.capture({})
