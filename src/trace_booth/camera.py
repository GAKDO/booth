"""C920 촬영. 장치·외부 명령은 함수 하나로 감싸 테스트에서 바꿔 끼운다."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from typing import Protocol

from .codes import Camera


class CameraError(Exception):
    def __init__(self, camera: Camera, reason: str) -> None:
        super().__init__(f"{camera.value}: {reason}")
        self.camera = camera
        self.reason = reason


class CameraDevice(Protocol):
    code: Camera

    def capture(self, settings: dict[str, int]) -> bytes:
        """설정을 적용하고 JPEG 한 장을 돌려준다. 실패하면 CameraError."""
        ...


def run_v4l2_ctl(device: str, controls: dict[str, int]) -> None:
    if not controls:
        return
    arg = ",".join(f"{k}={v}" for k, v in controls.items())
    subprocess.run(["v4l2-ctl", "-d", device, "-c", arg], check=True, capture_output=True, timeout=10)


class OpenCvCamera:
    """장치를 열고 프레임 몇 장을 버린 뒤 한 장 찍고 닫는다(두 대를 동시에 열지 않음)."""

    def __init__(
        self,
        code: Camera,
        device: str,
        width: int = 1920,
        height: int = 1080,
        warmup_frames: int = 10,
        set_controls: Callable[[str, dict[str, int]], None] = run_v4l2_ctl,
    ) -> None:
        self.code = code
        self.device = device
        self.width = width
        self.height = height
        self.warmup_frames = warmup_frames
        self._set_controls = set_controls

    def capture(self, settings: dict[str, int]) -> bytes:
        import cv2  # Jetson은 시스템 OpenCV를 쓰므로 필요할 때만 불러온다

        cap = cv2.VideoCapture(self.device, cv2.CAP_V4L2)
        if not cap.isOpened():
            raise CameraError(self.code, f"장치를 열 수 없음 ({self.device})")
        try:
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
            try:
                self._set_controls(self.device, settings)
            except (subprocess.SubprocessError, OSError) as e:
                raise CameraError(self.code, f"설정 적용 실패: {e}") from e
            for _ in range(self.warmup_frames):
                cap.read()
            ok, frame = cap.read()
            if not ok:
                raise CameraError(self.code, "프레임을 받지 못함")
            ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
            if not ok:
                raise CameraError(self.code, "JPEG 인코딩 실패")
            return buf.tobytes()
        finally:
            cap.release()
