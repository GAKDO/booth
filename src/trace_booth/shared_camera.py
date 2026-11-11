"""카메라를 한 번 열어 두고 실시간 보기와 촬영이 같이 쓴다(11/11).

jetson2에서 C920을 닫는 데(cap.release) 2~4초가 걸려, 실시간 보기와 촬영이 번갈아 열고 닫으면
단계마다 4초 넘게 기다렸다. 여기서는 1080p로 한 번 열어 두고
- 뒤 스레드만 장치를 만지며 프레임을 계속 받고(grab), 누가 원할 때만 방금 받은 것을 푼다(retrieve),
- 실시간 보기는 다음 프레임을 받아 줄여 보내고,
- 촬영은 프리셋을 넣고 프레임 몇 장을 흘려보낸 뒤 다음 프레임을 저장한다.
장치가 빠지거나 읽기가 끊기면 닫고 1초 뒤 다시 연다.
"""

from __future__ import annotations

import subprocess
import threading
import time
from collections.abc import Callable, Iterator
from typing import Any

from .camera import CameraError, _Marks, check_rotate, rotate_frame, run_v4l2_ctl
from .codes import Camera

OPEN_WAIT = 5.0  # 촬영할 때 장치가 열릴 때까지 기다리는 최대 초
FRAME_WAIT = 5.0  # 촬영할 때 새 프레임 하나를 기다리는 최대 초(노출 2047이면 프레임당 ~0.2초)


class SharedCamera:
    """한 장치를 계속 열어 두는 카메라. capture()는 CameraDevice, frames()는 실시간 보기."""

    def __init__(
        self,
        code: Camera,
        device: str,
        width: int = 1920,
        height: int = 1080,
        warmup_frames: int = 4,
        set_controls: Callable[[str, dict[str, int]], None] = run_v4l2_ctl,
        rotate: int = 0,
        live_width: int = 1280,
        reopen_delay: float = 1.0,
    ) -> None:
        self.code = code
        self.device = device
        self.width = width
        self.height = height
        self.warmup_frames = warmup_frames
        self.rotate = check_rotate(rotate)
        self.live_width = live_width
        self._set_controls = set_controls
        self._reopen_delay = reopen_delay
        self._cv = threading.Condition()
        self._cap: Any = None
        self._count = 0  # 받은 프레임 수(새 프레임을 기다릴 때 쓴다)
        self._want = 0  # 이 번호 이후 프레임을 풀어 달라는 요청(0이면 없음)
        self._frame: Any = None  # 마지막으로 푼 프레임
        self._frame_seq = 0
        self._shoot = threading.Lock()  # 촬영은 한 번에 하나
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # --- 열기·받기 ---
    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name=f"cam-{self.code.value}", daemon=True)
            self._thread.start()

    def stop(self, timeout: float = 10) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)

    def _open(self) -> Any:
        import cv2  # Jetson은 시스템 OpenCV를 쓰므로 필요할 때만 불러온다

        cap = cv2.VideoCapture(self.device, cv2.CAP_V4L2)
        if not cap.isOpened():
            cap.release()
            return None
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        return cap

    def _run(self) -> None:
        """장치는 이 스레드만 만진다. 받기를 계속하고, 누가 프레임을 원하면 방금 받은 것을 풀어 둔다."""
        while not self._stop.is_set():
            try:
                cap = self._open()
            except Exception as e:  # noqa: BLE001 - 열기 실패로 스레드가 죽으면 재시작 전까지 못 찍는다
                print(f"  {self.code.value} 열기 실패: {type(e).__name__}: {e}", flush=True)
                cap = None
            if cap is None:
                self._stop.wait(self._reopen_delay)
                continue
            print(f"  {self.code.value} 카메라를 열어 둠 ({self.device})", flush=True)
            with self._cv:
                self._cap = cap
                self._cv.notify_all()
            try:
                self._pump(cap)
            except Exception as e:  # noqa: BLE001 - 읽다가 난 오류도 닫고 다시 연다
                print(f"  {self.code.value} 읽기 오류: {type(e).__name__}: {e}. 다시 열어요", flush=True)
            finally:
                with self._cv:
                    self._cap = None
                    self._frame = None
                    self._cv.notify_all()
                cap.release()
            self._stop.wait(self._reopen_delay)

    def _pump(self, cap: Any) -> None:
        while not self._stop.is_set():
            if not cap.grab():
                print(f"  {self.code.value} 프레임이 끊김. 다시 열어요", flush=True)
                return
            with self._cv:
                self._count += 1
                want = self._want != 0 and self._count >= self._want
            if want:  # 푸는 일(디코딩)은 원할 때만 한다
                ok, frame = cap.retrieve()
                with self._cv:
                    self._frame = frame if ok else None
                    self._frame_seq = self._count
                    self._want = 0
                    self._cv.notify_all()
            else:
                with self._cv:
                    self._cv.notify_all()

    def _wait_open(self, timeout: float) -> Any:
        with self._cv:
            if not self._cv.wait_for(lambda: self._cap is not None or self._stop.is_set(), timeout):
                return None
            return self._cap

    def _frame_from(self, seq: int, timeout: float) -> Any:
        """seq번째 이후로 받은 프레임을 풀어 돌려준다. 장치가 닫혀 있거나, 못 풀거나, 시간이 지나면 None."""
        deadline = time.monotonic() + timeout
        with self._cv:
            while True:
                if self._cap is None:
                    return None
                if self._frame_seq >= seq:
                    return self._frame
                if self._want == 0 or self._want > seq:
                    self._want = seq
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._cv.wait(remaining)

    def _next_seq(self, skip: int = 0) -> int:
        """지금부터 skip장을 흘려보낸 다음 프레임의 번호."""
        with self._cv:
            return self._count + skip + 1

    # --- 촬영(CameraDevice) ---
    def capture(self, settings: dict[str, int]) -> bytes:
        import cv2

        marks = _Marks()
        try:
            with self._shoot:
                if self._wait_open(OPEN_WAIT) is None:
                    marks.lap("열기")
                    raise CameraError(self.code, f"장치를 열 수 없음 ({self.device})")
                marks.lap("대기")
                try:
                    self._set_controls(self.device, settings)
                except (subprocess.SubprocessError, OSError) as e:
                    marks.lap("설정")
                    raise CameraError(self.code, f"설정 적용 실패: {e}") from e
                marks.lap("설정")
                # 값이 반영된 프레임을 받기 위해 몇 장 흘려보내고, 그다음 새 프레임 한 장을 쓴다
                seq = self._next_seq(self.warmup_frames)
                frame = self._frame_from(seq, FRAME_WAIT * (self.warmup_frames + 1))
                marks.lap("버림+찍기")
                if frame is None:
                    raise CameraError(self.code, "프레임을 받지 못함")
                frame = rotate_frame(frame, self.rotate)
                ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
                marks.lap("인코딩")
                if not ok:
                    raise CameraError(self.code, "JPEG 인코딩 실패")
                return buf.tobytes()
        finally:
            print(f"  {self.code.value} {marks}", flush=True)

    # --- 실시간 보기 ---
    def live_jpeg(self) -> bytes | None:
        import cv2

        frame = self._frame_from(self._next_seq(), FRAME_WAIT)
        if frame is None:
            return None
        frame = rotate_frame(frame, self.rotate)
        h, w = frame.shape[:2]
        if w > self.live_width:
            frame = cv2.resize(frame, (self.live_width, int(h * self.live_width / w)), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
        return buf.tobytes() if ok else None

    def frames(
        self,
        current: Callable[[], bool] = lambda: True,
        fps: float = 10.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> Iterator[bytes]:
        """실시간 보기 JPEG를 끝없이 낸다. 촬영 중에도 멈추지 않는다.

        장치가 다시 열리는 동안에는 마지막 장면을 1초마다 다시 보내 화면과 연결을 살려 둔다.
        """
        last: bytes | None = None
        quiet = 0.0
        interval = 1.0 / fps
        while current():
            jpg = self.live_jpeg()
            if jpg is not None:
                last, quiet = jpg, 0.0
                yield jpg
            elif last is not None and quiet >= 1.0:
                quiet = 0.0
                yield last
            sleep(interval)
            quiet += interval
