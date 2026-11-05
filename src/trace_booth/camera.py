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
_TOLERANCE = 1  # 카메라가 gain 90을 89로 읽어 주는 일이 있다
# 화이트밸런스는 6500을 6480으로 읽어 주는 일이 있다(11/5 CAM2). 50K 차이는 색에 거의 영향이 없다
_TOLERANCES = {"white_balance_temperature": 50}


class ControlMismatch(subprocess.SubprocessError):
    """값을 넣었는데 카메라가 다른 값을 읽어 준다."""


def control_order(controls: dict[str, int]) -> tuple[dict[str, int], list[tuple[str, int]]]:
    """(먼저 켤 자동 컨트롤, 하나씩 넣을 값 순서).

    C920은 영상을 켜면 실제로는 자동 노출로 돌아가는데 드라이버는 여전히 수동이라고 읽힌다.
    이때 노출 값을 넣으면 거절되고, 여러 값을 한 번에 넣으면 gain을 빼먹거나 묶음 전체를
    거절하기도 한다(11/3 jetson2 실측). 그래서 수동으로 둘 컨트롤은 먼저 자동으로 켜고,
    자동 컨트롤부터 값을 하나씩 넣는다.
    """
    pre = {k: auto for k, (manual, auto) in _AUTO_TOGGLES.items() if controls.get(k) == manual}
    order = [(k, controls[k]) for k in _AUTO_TOGGLES if k in controls]
    order += [(k, v) for k, v in controls.items() if k not in _AUTO_TOGGLES]
    return pre, order


def parse_controls(text: str) -> dict[str, int]:
    """`v4l2-ctl -C` 출력("auto_exposure: 1 (Manual Mode)")을 {이름: 값}으로."""
    out: dict[str, int] = {}
    for line in text.splitlines():
        name, sep, rest = line.partition(":")
        parts = rest.split()
        if sep and parts and parts[0].lstrip("-").isdigit():
            out[name.strip()] = int(parts[0])
    return out


Runner = Callable[..., "subprocess.CompletedProcess[str]"]


def run_v4l2_ctl(device: str, controls: dict[str, int], run: Runner = subprocess.run) -> None:
    """값을 하나씩 넣고 읽어서 확인한다. 한 번 더 넣어도 다르면 ControlMismatch."""
    if not controls:
        return
    pre, order = control_order(controls)

    def put(name: str, value: int, check: bool) -> None:
        run(["v4l2-ctl", "-d", device, "-c", f"{name}={value}"],
            check=check, capture_output=True, text=True, timeout=10)

    def wrong() -> dict[str, int]:
        r = run(["v4l2-ctl", "-d", device, "-C", ",".join(controls)],
                check=True, capture_output=True, text=True, timeout=10)
        got = parse_controls(r.stdout)
        return {k: got.get(k, -1) for k, v in controls.items() if abs(got.get(k, -10_000) - v) > _TOLERANCES.get(k, _TOLERANCE)}

    for attempt in range(2):
        for name, value in pre.items():
            put(name, value, check=False)  # 이미 자동이면 거절될 수 있다. 다음 단계에서 확인한다
        for name, value in order:
            put(name, value, check=attempt == 1)
        bad = wrong()
        if not bad:
            return
    raise ControlMismatch(f"카메라 값이 들어가지 않음: {bad} (원하는 값 {controls})")


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
