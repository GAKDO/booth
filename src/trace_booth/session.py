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
from .shotlist import DEFAULT_SHOTLIST, QUICK_SHOTLIST, Step, StepKind
from .store import SessionRecord, ShotRecord, Store

MIN_FREE_BYTES = 200 * 1024 * 1024
MODES = ("TRAINING", "OUT", "RET", "QUICK")  # QUICK = 부스 빠른 검사(위·아래 2면, 11/3 MVP)
BOOTH_CODE_RE = re.compile(r"^[A-Z]{2,4}[0-9]{2}$")
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
    taken: bool = False  # 지금 단계를 이미 찍었는지(찍은 뒤 '다음'을 눌러야 넘어감)


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
        mode_steps: dict[str, tuple[Step, ...]] | None = None,
        training_lists: dict[str, tuple[Step, ...]] | None = None,
        default_list: str | None = None,
    ) -> None:
        self.store = store
        self.cameras = cameras
        self.presets = presets
        self.data_dir = data_dir
        data_dir.mkdir(parents=True, exist_ok=True)
        self.steps = steps  # 학습·출고·반납 목록
        self.mode_steps = {"QUICK": QUICK_SHOTLIST} if mode_steps is None else mode_steps  # 모드마다 다른 목록
        # 학습 모드에서 화면이 고를 수 있는 목록(11/6). 서버를 다시 켜지 않고 위·아래·펼친·옆면을 바꿔 찍는다
        self.training_lists = dict(training_lists or {})
        self.default_list = default_list
        self._now = now
        self._min_free = min_free_bytes
        self._lock = threading.Lock()

    def start(
        self, device_id: str, mode: str = "TRAINING", rental_id: str | None = None, checkpoint_id: str | None = None,
        shotlist: str | None = None,
    ) -> SessionRecord:
        """세션을 열거나 이어 간다. 출고·반납(OUT·RET)·빠른 검사(QUICK)는 렌탈·시점 publicId가 필요하다."""
        if not is_valid_device_id(device_id) or (mode == "TRAINING" and not BOOTH_CODE_RE.match(device_id)):
            # 학습 데이터는 ID 카드의 부스 코드만(trace-raw 키가 파일명 앞 `_`까지를 기기 ID로 씀)
            raise InvalidInput(f"기기 ID 형식이 아님: {device_id} (예: MB01, 빠른 검사는 목록의 기기)")
        if mode not in MODES:
            raise InvalidInput(f"모드는 {', '.join(MODES)} 중 하나")
        if mode != "TRAINING" or not shotlist or shotlist == self.default_list:
            shotlist = None  # 기본 목록은 비워 둔다(예전 세션과 같게)
        elif shotlist not in self.training_lists:
            raise InvalidInput(f"촬영 목록은 {', '.join(self.training_lists) or '기본'} 중 하나")
        if mode != "TRAINING":
            for name, value in (("렌탈", rental_id), ("시점", checkpoint_id)):
                if not value or not PUBLIC_ID_RE.match(value):
                    raise InvalidInput(f"{name} ID는 관리자 화면에 나온 21자 그대로 넣어 주세요")
        with self._lock:
            # 학습은 같은 목록의 열린 세션만 이어 간다. 다른 목록으로 찍던 세션은 그대로 두고 새로 연다
            s = self.store.find_open_session(device_id, mode, shotlist)
            if s is not None and mode != "TRAINING":
                c = self.store.get_checkout(s.id)
                if c is None or (c.rental_id, c.checkpoint_id) != (rental_id, checkpoint_id):
                    raise CaptureError("이 기기에 다른 렌탈·시점의 촬영이 진행 중이에요")
            if s is None:
                s = self.store.create_session(device_id, mode, self._now(), shotlist)
                if mode != "TRAINING":
                    self.store.add_checkout(s.id, str(rental_id), str(checkpoint_id))
            return s

    def steps_for(self, s: SessionRecord) -> tuple[Step, ...]:
        if s.mode == "TRAINING" and s.shotlist in self.training_lists:
            return self.training_lists[s.shotlist]
        return self.mode_steps.get(s.mode, self.steps)

    def view(self, session_id: int) -> SessionView:
        s = self.store.get_session(session_id)
        steps = self.steps_for(s)
        step = steps[s.current_step] if s.current_step < len(steps) else None
        taken = step is not None and self._taken(s, step)
        return SessionView(s, step, len(steps) - s.current_step, self.missing(session_id), taken)

    def shoot(self, session_id: int) -> list[ShotRecord]:
        with self._lock:
            s = self.store.get_session(session_id)
            step = self._current(s)
            if not step.captures:
                raise CaptureError("이 단계는 찍지 않는 안내 단계예요. '다음'을 누르세요")
            # 찍어도 단계는 그대로: 사진을 확인하고 '다음'을 눌러야 넘어간다(11/5, 조명을 바꾸기 전에 다음 단계 이름으로 찍히던 실수)
            return self._capture(s, step)

    def retake(self, session_id: int) -> list[ShotRecord]:
        """가장 최근에 찍은 단계(보통 지금 단계)를 새 번호로 다시 찍는다. 현재 단계는 그대로."""
        with self._lock:
            s = self.store.get_session(session_id)
            taken = self.store.shots(s.id)
            if not taken:
                raise CaptureError("다시 찍을 사진이 없어요")
            steps = self.steps_for(s)
            if taken[-1].step_index >= len(steps):
                raise CaptureError("촬영 목록이 바뀌어 다시 찍을 수 없어요. 새 촬영을 시작하세요")
            return self._capture(s, steps[taken[-1].step_index])

    def advance(self, session_id: int, expected_step: int | None = None) -> None:
        """다음 단계로. 찍는 단계는 그 단계의 카메라를 모두 찍은 뒤에만 넘어간다.

        expected_step을 주면 지금 단계가 그 번호일 때만 넘어간다(두 번 눌러 안내 단계까지 건너뛰지 않게).
        """
        with self._lock:
            s = self.store.get_session(session_id)
            step = self._current(s)
            if expected_step is not None and expected_step != step.index:
                raise CaptureError("이미 다음 단계로 넘어갔어요. 화면 안내를 확인하세요")
            if step.captures and not self._taken(s, step):
                raise CaptureError("이 단계를 아직 안 찍었어요. 촬영을 누르세요")
            self.store.update_session(s.id, current_step=s.current_step + 1)

    def _taken(self, s: SessionRecord, step: Step) -> bool:
        if not step.captures:
            return False
        have = {sh.camera for sh in self.store.shots(s.id) if sh.step_index == step.index}
        return all(cam.value in have for cam in step.cameras)

    def skip_optional(self, session_id: int, reason: str) -> None:
        if not reason.strip():
            raise InvalidInput("건너뛰는 사유를 적어 주세요")
        with self._lock:
            s = self.store.get_session(session_id)
            step = self._current(s)
            if not step.optional:
                raise CaptureError("건너뛸 수 있는 단계가 아니에요")
            if any(sh.step_index == step.index for sh in self.store.shots(s.id)):
                raise CaptureError("이 단계는 이미 찍었어요. '다음'을 누르세요")
            self.store.update_session(s.id, scr1_skip_reason=reason.strip(), current_step=s.current_step + 1)

    def missing(self, session_id: int) -> list[str]:
        s = self.store.get_session(session_id)
        have = {(sh.step_index, sh.camera) for sh in self.store.shots(session_id)}
        out = []
        for step in self.steps_for(s):
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
        steps = self.steps_for(s)
        if s.status != "IN_PROGRESS" or s.current_step >= len(steps):
            raise CaptureError("촬영 목록이 끝났어요. 완료를 누르세요")
        return steps[s.current_step]

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
