"""실시간 카메라 보기(MJPEG). 촬영할 때는 장치를 놓아 주고, 촬영이 끝나면 다시 연다.

카메라 장치는 한 번에 한 곳만 열 수 있어서, 촬영(OpenCvCamera.capture)과 실시간 보기가
CameraGate 하나를 함께 쓴다. 촬영이 시작되면 실시간 보기는 다음 프레임에서 장치를 닫고
촬영이 끝날 때까지 기다린다.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Protocol

from .camera import CameraError
from .codes import Camera


class CameraGate:
    """촬영이 실시간 보기보다 먼저 장치를 쓰게 한다."""

    def __init__(self) -> None:
        self._cv = threading.Condition()
        self._pending = 0  # 기다리거나 찍는 중인 촬영 수
        self._live_open = 0  # 장치를 열고 있는 실시간 보기 수

    @contextmanager
    def exclusive(self, timeout: float = 5.0) -> Iterator[None]:
        """촬영용. 실시간 보기가 장치를 닫을 때까지 기다린 뒤 들어간다."""
        with self._cv:
            self._pending += 1
            self._cv.wait_for(lambda: self._live_open == 0, timeout)
        try:
            yield
        finally:
            with self._cv:
                self._pending -= 1
                self._cv.notify_all()

    def live_acquire(self, timeout: float) -> bool:
        """실시간 보기용. 촬영이 없을 때만 장치를 열어도 된다."""
        with self._cv:
            if not self._cv.wait_for(lambda: self._pending == 0, timeout):
                return False
            self._live_open += 1
            return True

    def live_release(self) -> None:
        with self._cv:
            self._live_open -= 1
            self._cv.notify_all()

    def capture_pending(self) -> bool:
        with self._cv:
            return self._pending > 0


class FrameSource(Protocol):
    def read(self) -> bytes | None:
        """JPEG 한 장. 받지 못하면 None."""
        ...

    def close(self) -> None: ...


class OpenCvLiveSource:
    """작은 해상도로 장치를 열어 JPEG 프레임을 돌려준다."""

    def __init__(self, code: Camera, device: str, width: int = 1280, height: int = 720) -> None:
        import cv2  # Jetson은 시스템 OpenCV를 쓰므로 필요할 때만 불러온다

        self._cv2 = cv2
        self._cap = cv2.VideoCapture(device, cv2.CAP_V4L2)
        if not self._cap.isOpened():
            raise CameraError(code, f"장치를 열 수 없음 ({device})")
        self._cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)

    def read(self) -> bytes | None:
        ok, frame = self._cap.read()
        if not ok:
            return None
        ok, buf = self._cv2.imencode(".jpg", frame, [self._cv2.IMWRITE_JPEG_QUALITY, 70])
        return buf.tobytes() if ok else None

    def close(self) -> None:
        self._cap.release()


def live_frames(
    open_source: Callable[[], FrameSource],
    gate: CameraGate,
    fps: float = 10.0,
    sleep: Callable[[float], None] = time.sleep,
) -> Iterator[bytes]:
    """JPEG 프레임을 끝없이 낸다. 촬영이 끼어들면 장치를 닫고 기다렸다가 다시 연다.

    받는 쪽이 끊으면(제너레이터 close) 장치를 닫는다.
    """
    interval = 1.0 / fps
    while True:
        if not gate.live_acquire(timeout=1.0):
            continue
        try:
            try:
                source = open_source()
            except CameraError:
                sleep(1.0)  # 다른 곳이 장치를 쓰는 중. 잠시 뒤 다시
                continue
            try:
                while not gate.capture_pending():
                    jpg = source.read()
                    if jpg is None:
                        sleep(1.0)  # 프레임이 끊김. 장치를 다시 연다
                        break
                    yield jpg
                    sleep(interval)
            finally:
                source.close()
        finally:
            gate.live_release()
