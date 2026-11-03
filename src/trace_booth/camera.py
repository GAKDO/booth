"""C920 촬영. 장치·외부 명령은 함수 하나로 감싸 테스트에서 바꿔 끼운다."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from contextlib import nullcontext
from typing import TYPE_CHECKING, Protocol

from .codes import Camera

if TYPE_CHECKING:
    from .live import CameraGate


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


# 수동으로 둘 때 먼저 자동으로 한 번 켜 줄 컨트롤: {자동 컨트롤: (수동 값, 자동 값)}
_AUTO_TOGGLES = {
    "auto_exposure": (1, 3),
    "white_balance_automatic": (0, 1),
}


def control_steps(controls: dict[str, int]) -> list[dict[str, int]]:
    """v4l2-ctl에 차례로 넣을 묶음들.

    C920은 영상을 켜면 실제로는 자동 노출로 돌아가는데 드라이버는 여전히 수동이라고 읽힌다.
    이때 노출 값을 넣으면 Permission denied로 거절된다. 자동을 한 번 켰다가 수동으로 바꾸면
    값이 먹는다(11/3 jetson2 실측). 그래서 수동으로 둘 컨트롤은 먼저 자동으로 켜고,
    다음 묶음에서 자동 컨트롤을 앞에 두고 값을 넣는다.
    """
    if not controls:
        return []
    pre = {k: auto for k, (manual, auto) in _AUTO_TOGGLES.items() if controls.get(k) == manual}
    ordered = {k: controls[k] for k in _AUTO_TOGGLES if k in controls}
    ordered.update({k: v for k, v in controls.items() if k not in ordered})
    return [pre, ordered] if pre else [ordered]


def run_v4l2_ctl(device: str, controls: dict[str, int]) -> None:
    for step in control_steps(controls):
        arg = ",".join(f"{k}={v}" for k, v in step.items())
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
        gate: CameraGate | None = None,
    ) -> None:
        self.code = code
        self.device = device
        self.width = width
        self.height = height
        self.warmup_frames = warmup_frames
        self._set_controls = set_controls
        self._gate = gate  # 실시간 보기와 장치를 나눠 쓸 때. 찍는 동안 실시간 보기를 멈춘다

    def capture(self, settings: dict[str, int]) -> bytes:
        with self._gate.exclusive() if self._gate is not None else nullcontext():
            return self._capture(settings)

    def _capture(self, settings: dict[str, int]) -> bytes:
        import cv2  # Jetson은 시스템 OpenCV를 쓰므로 필요할 때만 불러온다

        cap = cv2.VideoCapture(self.device, cv2.CAP_V4L2)
        if not cap.isOpened():
            raise CameraError(self.code, f"장치를 열 수 없음 ({self.device})")
        try:
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
            # 영상을 켠 뒤에 값을 넣는다. 켜기 전에 넣으면 카메라가 영상을 켜면서 자동으로 되돌린다
            cap.read()
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
