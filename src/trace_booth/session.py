"""촬영 진행: 단계 안내, 찍기, 재촬영, SCR1 건너뛰기, 완료 검사."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .camera import CameraDevice
from .camera_presets import Presets
from .codes import Camera, Light
from .naming import is_valid_device_id, shot_filename, slate_filename
from .shotlist import (
    DEFAULT_SHOTLIST,
    FULL_INSPECTION,
    FULL_INSPECTION_SHOTLIST,
    PROFILE_FULL,
    PROFILE_QUICK,
    QUICK_SHOTLIST,
    SIDE_FACES,
    Step,
    StepKind,
    lights_used,
)
from .store import SessionRecord, ShotRecord, Store

MIN_FREE_BYTES = 200 * 1024 * 1024
MODES = ("TRAINING", "OUT", "RET", "QUICK")  # QUICK = 부스 검사(기본 위·아래 2면, 11/3 MVP. 전체 검사도 이 흐름)
PROFILES = (PROFILE_QUICK, PROFILE_FULL)  # 검사 모드의 세트 범위(codes.yaml capture.profile)
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


def _same_step(shot: ShotRecord, step: Step) -> bool:
    """사진이 이 단계의 것인지. 단계 번호만이 아니라 면·조명까지 맞춰 본다.

    서버를 다시 켜 목록 순서가 바뀌어도(11/11 옆면 순서) 같은 번호의 다른 조명 사진을 이 단계 것으로 치지 않는다.
    """
    face = step.face.value if step.face else None
    light = step.light.value if step.light else None
    return shot.step_index == step.index and shot.face == face and shot.light == light


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
        list_cameras: dict[str, dict[Camera, CameraDevice]] | None = None,
        list_presets: dict[str, Presets] | None = None,
        inspection_lists: dict[str, tuple[Step, ...]] | None = None,
        default_profile: str = PROFILE_QUICK,
        parallel_capture: bool = True,
    ) -> None:
        self.store = store
        # 한 단계의 카메라들을 동시에 찍는다(11/9, 시간 절반). USB가 버거우면 False로 한 대씩
        self.parallel_capture = parallel_capture
        self.cameras = cameras
        self.presets = presets
        self.data_dir = data_dir
        data_dir.mkdir(parents=True, exist_ok=True)
        self.steps = steps  # 학습·출고·반납 목록
        self.mode_steps = {"QUICK": QUICK_SHOTLIST} if mode_steps is None else mode_steps  # 모드마다 다른 목록
        # 학습 모드에서 화면이 고를 수 있는 목록(11/6). 서버를 다시 켜지 않고 위·아래·펼친·옆면을 바꿔 찍는다
        self.training_lists = dict(training_lists or {})
        self.default_list = default_list
        # 목록마다 바꿔 끼울 카메라·프리셋(11/6). 옆면(side)은 책상 높이의 Brio가 CAM1 자리를 맡는다.
        # 파일 이름의 카메라 코드는 그대로(CAM1) 두어 계약(codes.yaml)을 바꾸지 않는다
        self.list_cameras = dict(list_cameras or {})
        self.list_presets = dict(list_presets or {})
        # 검사 모드(QUICK 흐름)에서 고를 수 있는 다른 세트 범위(11/8): 이름 → 목록. 지금은 전체 검사(full)뿐
        self.inspection_lists = (
            {FULL_INSPECTION: FULL_INSPECTION_SHOTLIST} if inspection_lists is None else dict(inspection_lists)
        )
        if default_profile not in PROFILES:
            raise ValueError(f"세트 범위는 {', '.join(PROFILES)} 중 하나: {default_profile}")
        self.default_profile = default_profile  # 화면이 고르지 않으면 쓰는 범위. 기본은 빠른 검사(시연 그대로)
        self._now = now
        self._min_free = min_free_bytes
        self._lock = threading.Lock()

    def start(
        self, device_id: str, mode: str = "TRAINING", rental_id: str | None = None, checkpoint_id: str | None = None,
        shotlist: str | None = None, profile: str | None = None,
    ) -> SessionRecord:
        """세션을 열거나 이어 간다. 출고·반납(OUT·RET)·빠른 검사(QUICK)는 렌탈·시점 publicId가 필요하다.

        profile은 검사 모드의 세트 범위(QUICK·FULL). 빼면 default_profile. 다른 모드에서는 무시한다.
        """
        if not is_valid_device_id(device_id) or (mode == "TRAINING" and not BOOTH_CODE_RE.match(device_id)):
            # 학습 데이터는 ID 카드의 부스 코드만(trace-raw 키가 파일명 앞 `_`까지를 기기 ID로 씀)
            raise InvalidInput(f"기기 ID 형식이 아님: {device_id} (예: MB01, 빠른 검사는 목록의 기기)")
        if mode not in MODES:
            raise InvalidInput(f"모드는 {', '.join(MODES)} 중 하나")
        # 학습 세션에는 기본 목록이어도 실제 이름을 남긴다. 서버를 다른 기본 목록으로 다시 켜도
        # 예전 세션(이름 없음·다른 목록)이 새 목록의 단계로 이어지지 않게 하려고
        if mode == "QUICK":
            shotlist = self._inspection_list(profile)
        elif mode != "TRAINING" or not self.training_lists:
            shotlist = None
        else:
            shotlist = shotlist or self.default_list
            if shotlist not in self.training_lists:
                raise InvalidInput(f"촬영 목록은 {', '.join(self.training_lists)} 중 하나")
        if mode != "TRAINING":
            for name, value in (("렌탈", rental_id), ("시점", checkpoint_id)):
                if not value or not PUBLIC_ID_RE.match(value):
                    raise InvalidInput(f"{name} ID는 관리자 화면에 나온 21자 그대로 넣어 주세요")
        with self._lock:
            # 학습은 같은 목록의 열린 세션만 이어 간다. 다른 목록으로 찍던 세션은 그대로 두고 새로 연다
            s = self.store.find_open_session(device_id, mode, shotlist)
            if s is not None and not self._list_matches(s):
                # 서버를 다시 켜 목록 순서가 바뀐 세션(11/11 옆면 순서)은 닫고 처음부터 새로 찍는다
                self.store.update_session(s.id, status="ABANDONED")
                s = None
            if s is not None and mode != "TRAINING":
                c = self.store.get_checkout(s.id)
                if c is None or (c.rental_id, c.checkpoint_id) != (rental_id, checkpoint_id):
                    raise CaptureError("이 기기에 다른 렌탈·시점의 촬영이 진행 중이에요")
            if s is None:
                s = self.store.create_session(device_id, mode, self._now(), shotlist)
                if mode != "TRAINING":
                    self.store.add_checkout(s.id, str(rental_id), str(checkpoint_id))
            return s

    def _inspection_list(self, profile: str | None) -> str | None:
        """검사 세트 범위 → 세션에 남길 목록 이름(빠른 검사는 None). 쓸 수 없는 범위면 400."""
        profile = (profile or self.default_profile).upper()
        if profile not in PROFILES:
            raise InvalidInput(f"검사 범위는 {', '.join(PROFILES)} 중 하나예요")
        if profile == PROFILE_QUICK:
            return None
        if FULL_INSPECTION not in self.inspection_lists:
            raise InvalidInput("이 부스에는 전체 검사 목록이 없어요")
        steps = self.inspection_lists[FULL_INSPECTION]
        # 프리셋 파일에 KB(L4)·SCR1(OFF) 조명 값이 없으면 중간에 막히므로 시작 전에 알린다
        missing = set()
        for presets, side in ((self.presets, False), (self.list_presets.get("side", self.presets), True)):
            if presets.configured:
                need = lights_used(tuple(st for st in steps if (st.face in SIDE_FACES) is side))
                missing |= {li.value for li in need if li.value not in presets.by_light}
        if missing:
            raise InvalidInput(f"프리셋 파일에 전체 검사 조명 {', '.join(sorted(missing))} 값이 없어요")
        return FULL_INSPECTION

    def steps_for(self, s: SessionRecord) -> tuple[Step, ...]:
        if s.mode == "TRAINING" and s.shotlist in self.training_lists:
            return self.training_lists[s.shotlist]
        if s.mode == "QUICK" and s.shotlist in self.inspection_lists:
            return self.inspection_lists[s.shotlist]
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
            self._check_list_unchanged(s)
            step = self._current(s)
            if not step.captures:
                raise CaptureError("이 단계는 찍지 않는 안내 단계예요. '다음'을 누르세요")
            # 찍어도 단계는 그대로: 사진을 확인하고 '다음'을 눌러야 넘어간다(11/5, 조명을 바꾸기 전에 다음 단계 이름으로 찍히던 실수)
            return self._capture(s, step)

    def retake(self, session_id: int) -> list[ShotRecord]:
        """지금 단계를 찍었으면 그 단계를, 아니면 가장 최근에 찍은 단계를 새 번호로 다시 찍는다. 현재 단계는 그대로.

        '이전 단계'로 돌아온 뒤 다시 찍기를 누르면 돌아온 그 단계를 다시 찍어야 한다(11/10).
        """
        with self._lock:
            s = self.store.get_session(session_id)
            taken = self.store.shots(s.id)
            if not taken:
                raise CaptureError("다시 찍을 사진이 없어요")
            self._check_list_unchanged(s)
            steps = self.steps_for(s)
            if s.status == "IN_PROGRESS" and s.current_step < len(steps) and self._taken(s, steps[s.current_step]):
                return self._capture(s, steps[s.current_step])
            if taken[-1].step_index >= len(steps) or not _same_step(taken[-1], steps[taken[-1].step_index]):
                # 서버를 다시 켜 목록 순서가 바뀌면(11/11 옆면 순서) 같은 번호가 다른 조명을 가리킨다
                raise CaptureError("촬영 목록이 바뀌어 다시 찍을 수 없어요. 새 촬영을 시작하세요")
            if s.status == "IN_PROGRESS" and taken[-1].step_index > s.current_step:
                # 이전 단계로 돌아온 뒤 뒤쪽 단계를 지금 조명으로 다시 찍으면 사진이 엉뚱한 이름으로 남는다
                raise CaptureError("이 단계는 아직 안 찍었어요. 촬영을 누르세요")
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
            self._prepare(s.id)

    def back(self, session_id: int, expected_step: int | None = None) -> list[ShotRecord]:
        """이전 단계로 돌아간다. 돌아간 단계에서 마지막으로 찍은 사진들을 돌려준다(화면에 다시 보이게).

        잘못 찍고 '다음'을 눌렀을 때 돌아가서 다시 찍기 위해(11/10). 앞에서 찍은 사진은 지우지 않고,
        다시 찍으면 새 번호로 남아 올리기·탐지는 마지막 장(latest_shots)만 쓴다.
        expected_step을 주면 지금 단계가 그 번호일 때만 돌아간다(목록 끝이면 단계 수와 같은 번호).
        """
        with self._lock:
            s = self.store.get_session(session_id)
            steps = self.steps_for(s)
            if s.status != "IN_PROGRESS":
                raise CaptureError("이미 완료한 촬영이에요")
            if expected_step is not None and expected_step != s.current_step:
                raise CaptureError("이미 다른 단계로 바뀌었어요. 화면 안내를 확인하세요")
            if s.current_step <= 0:
                raise CaptureError("첫 단계예요. 더 돌아갈 단계가 없어요")
            prev = min(s.current_step, len(steps)) - 1
            self.store.update_session(s.id, current_step=prev)
            self._prepare(s.id)
            latest: dict[str, ShotRecord] = {}
            for sh in self.store.shots(s.id):
                if _same_step(sh, steps[prev]):
                    latest[sh.camera] = sh  # 찍은 차례대로라 마지막 것이 남는다
            return list(latest.values())

    def _check_list_unchanged(self, s: SessionRecord) -> None:
        """찍은 사진이 지금 목록의 같은 번호 단계와 면·조명이 다르면 이 세션을 닫고 멈춘다.

        서버를 다시 켜 목록 순서가 바뀐 세션(11/11 옆면 순서)에서 화면 안내와 실제 조명이 어긋난 채 찍히는 것을 막는다.
        세션을 ABANDONED로 닫아 두므로 같은 기기를 다시 고르면 새 세션으로 처음부터 찍는다.
        """
        if not self._list_matches(s):
            if s.status == "IN_PROGRESS":
                self.store.update_session(s.id, status="ABANDONED")
            raise CaptureError("촬영 목록이 바뀌어 이 촬영을 닫았어요. 처음 화면에서 기기를 다시 골라 처음부터 찍어 주세요")

    def _list_matches(self, s: SessionRecord) -> bool:
        """이 세션의 사진이 모두 지금 목록의 같은 번호 단계와 면·조명이 맞는지."""
        steps = self.steps_for(s)
        return all(sh.step_index < len(steps) and _same_step(sh, steps[sh.step_index]) for sh in self.store.shots(s.id))

    def _taken(self, s: SessionRecord, step: Step) -> bool:
        if not step.captures:
            return False
        have = {sh.camera for sh in self.store.shots(s.id) if _same_step(sh, step)}
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
        shots = self.store.shots(session_id)
        out = []
        for step in self.steps_for(s):
            if step.kind is not StepKind.SHOT:
                continue
            for cam in step.cameras:
                if any(sh.camera == cam.value and _same_step(sh, step) for sh in shots):
                    continue
                if step.optional and s.scr1_skip_reason:
                    continue
                out.append(f"{step.face.value}_{step.light.value}_{cam.value}")
        return out

    def complete(self, session_id: int) -> SessionRecord:
        with self._lock:
            self._check_list_unchanged(self.store.get_session(session_id))
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

    def _grab(self, cams: tuple[Camera, ...], swap: dict[Camera, CameraDevice],
              settings: dict[str, int]) -> list[bytes]:
        devices = [swap.get(cam, self.cameras[cam]) for cam in cams]
        if not self.parallel_capture or len(devices) < 2:
            return [d.capture(settings) for d in devices]
        with ThreadPoolExecutor(max_workers=len(devices)) as pool:
            futures = [pool.submit(d.capture, settings) for d in devices]
            return [f.result() for f in futures]  # 실패는 카메라 순서대로 처음 것을 올린다

    @staticmethod
    def _list_key(s: SessionRecord, step: Step) -> str:
        if s.mode == "TRAINING":
            return s.shotlist or ""
        if s.mode == "QUICK" and s.shotlist and step.face in SIDE_FACES:
            return "side"  # 전체 검사의 옆면은 옆면 카메라가 있으면 그것으로(없으면 CAM1을 내려서)
        return ""

    def _prepare(self, session_id: int) -> None:
        """다음에 찍을 단계의 카메라를 미리 연다(11/12 위·옆면 카메라 번갈아 열기).

        안내 단계(노트북을 옆면 카메라 앞에 세우기)에서 옆면 실시간 화면이 바로 보이고, 첫 옆면 촬영이
        카메라를 바꾸느라 기다리지 않게 한다. 바꾸기는 몇 초 걸리므로 뒤에서 한다.
        """
        s = self.store.get_session(session_id)
        steps = self.steps_for(s)
        step = next((st for st in steps[s.current_step:] if st.captures), None)
        if s.status != "IN_PROGRESS" or step is None:
            return
        swap = self.list_cameras.get(self._list_key(s, step), {})
        for cam in step.cameras:
            hook = getattr(swap.get(cam, self.cameras.get(cam)), "before_use", None)
            if hook is not None:
                threading.Thread(target=hook, name="cam-prepare", daemon=True).start()
                return  # 한 묶음은 한 번만 바꾸면 된다

    def _capture(self, s: SessionRecord, step: Step) -> list[ShotRecord]:
        if shutil.disk_usage(self.data_dir).free < self._min_free:
            raise DiskFull("디스크 공간이 부족해요. 찍지 않았어요")
        light = step.light or Light.L1
        key = self._list_key(s, step)
        swap = self.list_cameras.get(key, {})
        presets = self.list_presets.get(key, self.presets) if swap else self.presets
        settings = presets.for_light(light)
        started = time.monotonic()
        images = self._grab(step.cameras, swap, settings)  # 한 대라도 실패하면 아무것도 남기지 않는다(짝 없는 사진 방지)
        print(f"촬영 {step.index}번 단계 {len(images)}장 {time.monotonic() - started:.1f}초", flush=True)
        shots = []
        for cam, data in zip(step.cameras, images):
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
