"""촬영 진행: 단계 안내, 찍기, 재촬영, SCR1 건너뛰기, 완료 검사."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .camera import CameraDevice
from .camera_presets import Presets
from .codes import Camera, Light
from .naming import is_valid_device_id, shot_filename, slate_filename
from .shotlist import DEFAULT_SHOTLIST, Step, StepKind
from .store import SessionRecord, ShotRecord, Store

MIN_FREE_BYTES = 200 * 1024 * 1024
MODES = ("TRAINING", "OUT", "RET")
PUBLIC_ID_RE = re.compile(r"^[A-Za-z0-9_-]{21}$")  # 다른 서버의 publicId(NanoID 21자)


class CaptureError(Exception):
    """운영 화면에 그대로 보여 줄 수 있는 촬영 오류."""

    status = 409


class InvalidInput(CaptureError):
    status = 400


class DiskFull(CaptureError):
    status = 507


class Incomplete(CaptureError):
    def __init__(self, missing: list[str]) -> None:
        super().__init__(f"빠진 사진 {len(missing)}개")
        self.missing = missing


@dataclass
class SessionView:
    session: SessionRecord
    step: Step | None
    remaining_steps: int
    missing: list[str]


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _write_new(path: Path, data: bytes) -> None:
    """새 파일로만 쓴다(있으면 실패). fsync까지 끝난 뒤에 기록을 남긴다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "xb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


class CaptureService:
    def __init__(
        self,
        store: Store,
        cameras: dict[Camera, CameraDevice],
        presets: Presets,
        data_dir: Path,
        steps: tuple[Step, ...] = DEFAULT_SHOTLIST,
        now: Callable[[], str] = _now,
        min_free_bytes: int = MIN_FREE_BYTES,
    ) -> None:
        self.store = store
        self.cameras = cameras
        self.presets = presets
        self.data_dir = data_dir
        data_dir.mkdir(parents=True, exist_ok=True)
        self.steps = steps
        self._now = now
        self._min_free = min_free_bytes
        self._lock = threading.Lock()

    def start(
        self, device_id: str, mode: str = "TRAINING", rental_id: str | None = None, checkpoint_id: str | None = None
    ) -> SessionRecord:
        """세션을 열거나 이어 간다. 출고·반납(OUT·RET)은 관리자 화면이 고른 렌탈·시점 publicId가 필요하다."""
        if not is_valid_device_id(device_id):
            raise InvalidInput(f"기기 ID 형식이 아님: {device_id} (예: MB01)")
        if mode not in MODES:
            raise InvalidInput(f"모드는 {', '.join(MODES)} 중 하나")
        if mode != "TRAINING":
            for name, value in (("렌탈", rental_id), ("시점", checkpoint_id)):
                if not value or not PUBLIC_ID_RE.match(value):
                    raise InvalidInput(f"{name} ID는 관리자 화면에 나온 21자 그대로 넣어 주세요")
        with self._lock:
            s = self.store.find_open_session(device_id, mode)
            if s is not None and mode != "TRAINING":
                c = self.store.get_checkout(s.id)
                if c is None or (c.rental_id, c.checkpoint_id) != (rental_id, checkpoint_id):
                    raise CaptureError("이 기기에 다른 렌탈·시점의 촬영이 진행 중이에요")
            if s is None:
                s = self.store.create_session(device_id, mode, self._now())
                if mode != "TRAINING":
                    self.store.add_checkout(s.id, str(rental_id), str(checkpoint_id))
            return s

    def view(self, session_id: int) -> SessionView:
        s = self.store.get_session(session_id)
        step = self.steps[s.current_step] if s.current_step < len(self.steps) else None
        return SessionView(s, step, len(self.steps) - s.current_step, self.missing(session_id))

    def shoot(self, session_id: int) -> list[ShotRecord]:
        with self._lock:
            s = self.store.get_session(session_id)
            step = self._current(s)
            if not step.captures:
                raise CaptureError("이 단계는 찍지 않는 안내 단계예요. '다음'을 누르세요")
            shots = self._capture(s, step)
            self.store.update_session(s.id, current_step=s.current_step + 1)
            return shots

    def retake(self, session_id: int) -> list[ShotRecord]:
        """가장 최근에 찍은 단계를 새 번호로 다시 찍는다. 현재 단계는 그대로."""
        with self._lock:
            s = self.store.get_session(session_id)
            taken = self.store.shots(s.id)
            if not taken:
                raise CaptureError("다시 찍을 사진이 없어요")
            if taken[-1].step_index >= len(self.steps):
                raise CaptureError("촬영 목록이 바뀌어 다시 찍을 수 없어요. 새 촬영을 시작하세요")
            return self._capture(s, self.steps[taken[-1].step_index])

    def acknowledge(self, session_id: int) -> None:
        with self._lock:
            s = self.store.get_session(session_id)
            if self._current(s).kind is not StepKind.NOTICE:
                raise CaptureError("안내 단계가 아니에요. 촬영 버튼을 누르세요")
            self.store.update_session(s.id, current_step=s.current_step + 1)

    def skip_optional(self, session_id: int, reason: str) -> None:
        if not reason.strip():
            raise InvalidInput("건너뛰는 사유를 적어 주세요")
        with self._lock:
            s = self.store.get_session(session_id)
            if not self._current(s).optional:
                raise CaptureError("건너뛸 수 있는 단계가 아니에요")
            self.store.update_session(s.id, scr1_skip_reason=reason.strip(), current_step=s.current_step + 1)

    def missing(self, session_id: int) -> list[str]:
        s = self.store.get_session(session_id)
        have = {(sh.step_index, sh.camera) for sh in self.store.shots(session_id)}
        out = []
        for step in self.steps:
            if step.kind is not StepKind.SHOT:
                continue
            for cam in step.cameras:
                if (step.index, cam.value) in have:
                    continue
                if step.optional and s.scr1_skip_reason:
                    continue
                out.append(f"{step.face.value}_{step.light.value}_{cam.value}")
        return out

    def complete(self, session_id: int) -> SessionRecord:
        with self._lock:
            missing = self.missing(session_id)
            if missing:
                raise Incomplete(missing)
            self.store.update_session(session_id, status="COMPLETE", completed_at=self._now())
            return self.store.get_session(session_id)

    def _current(self, s: SessionRecord) -> Step:
        if s.status != "IN_PROGRESS" or s.current_step >= len(self.steps):
            raise CaptureError("촬영 목록이 끝났어요. 완료를 누르세요")
        return self.steps[s.current_step]

    def _capture(self, s: SessionRecord, step: Step) -> list[ShotRecord]:
        if shutil.disk_usage(self.data_dir).free < self._min_free:
            raise DiskFull("디스크 공간이 부족해요. 찍지 않았어요")
        light = step.light or Light.L1
        settings = self.presets.for_light(light)
        shots = []
        for cam in step.cameras:
            data = self.cameras[cam].capture(settings)
            kind = step.kind.value
            face = step.face.value if step.face else None
            seq = self.store.next_seq(s.device_id, kind, face, step.light.value if step.light else None, cam.value)
            if step.kind is StepKind.SLATE:
                name = slate_filename(s.device_id, seq)
                path = self.data_dir / s.device_id / "slate" / name
            else:
                name = shot_filename(s.device_id, step.face, step.light, cam, seq)
                path = self.data_dir / s.device_id / name
            _write_new(path, data)
            shot = ShotRecord(
                session_id=s.id,
                step_index=step.index,
                kind=kind,
                face=face,
                light=step.light.value if step.light else None,
                camera=cam.value,
                seq=seq,
                filename=name,
                local_path=str(path),
                sha256=hashlib.sha256(data).hexdigest(),
                size_bytes=len(data),
                camera_settings=json.dumps(settings, sort_keys=True),
                captured_at=self._now(),
            )
            self.store.add_shot(shot)
            shots.append(shot)
        return shots
