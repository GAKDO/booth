"""빠른 검사(11/3 MVP): 완료를 누르면 BOT·TOP 사진을 Jetson 탐지기에 넣고 부스 화면에 "문제 있음/없음"을 보인다.

판정 시간 줄이기(11/6): 찍을 때마다 그 사진을 뒤에서 미리 탐지해 두고(BOT을 뒤집는 사이 BOT 6장이 끝난다), 완료 때는
남은 사진만 돌린다. 판정 조명을 골라(예: L3만) 12장 대신 4장만 볼 수도 있다.

탐지기는 `DetectorSource` 하나로 감싸 테스트·엔진 도착 전에는 가짜로 바꿔 끼운다. 사진은 읽기만 하고, 탐지에
실패해도 사진과 세션은 그대로 남아 다시 돌릴 수 있다.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from typing import Any

from .codes import DEFECT_CLASSES
from .detect.engine import DetectError, Detector, ModelInfo, RawDetection
from .detect.runner import ImageLoader, PhotoResult, detect_set, detect_shot, judged_shots
from .shotlist import PROFILE_QUICK, inspection_profile
from .store import ShotRecord, Store

DetectorSource = Callable[[], tuple[Detector, ModelInfo]]  # 엔진을 불러 (탐지기, 모델 정보). 못 부르면 DetectError
AfterDetect = Callable[[int], None]  # 탐지가 끝난 세션(내부 id)으로 할 일(서버 올리기)

NO_ENGINE_VERSION = "no-engine"


class NoEngineDetector:
    """엔진 없이 흐름만 시험할 때(TRACE_BOOTH_DETECTOR=none). 늘 흠집 0개라 화면에 시험 표시가 붙는다."""

    def infer(self, tile: Any) -> list[RawDetection]:
        return []


def no_engine_source() -> tuple[Detector, ModelInfo]:
    return NoEngineDetector(), ModelInfo(NO_ENGINE_VERSION, DEFECT_CLASSES)


class QuickInspector:
    def __init__(
        self,
        store: Store,
        source: DetectorSource,
        load: ImageLoader,
        now: Callable[[], str],
        after: AfterDetect | None = None,
        background: bool = True,
        clock: Callable[[], float] = time.perf_counter,
        lights: frozenset[str] | None = None,
        predetect: bool = True,
    ) -> None:
        self.store = store
        self._source = source
        self._load = load
        self._now = now
        self.after = after
        self._background = background
        self._clock = clock
        self._detector: tuple[Detector, ModelInfo] | None = None
        self._lock = threading.Lock()  # 엔진은 하나라 한 번에 한 세트(또는 미리 탐지 한 장)만 돌린다
        self.lights = lights  # 빠른 검사의 판정 조명(None이면 전부). 전체 검사는 늘 전부
        self.predetect = predetect
        self._done: dict[int, dict[str, PhotoResult]] = {}  # 세션 → 사진 경로 → 미리 탐지한 결과

    def warm(self) -> None:
        """serve를 띄울 때 엔진을 미리 불러 첫 검사의 판정 시간을 줄인다. 실패는 검사 때 다시 알린다."""
        try:
            with self._lock:
                self._get()
        except DetectError:
            pass

    def start(self, session_id: int) -> None:
        """탐지를 시작한다(이미 돌고 있으면 그대로). background면 바로 돌아오고 화면은 result를 다시 읽는다."""
        current = self.store.get_quick_result(session_id)
        if current is not None and current.status == "DETECTING":
            return
        self.store.put_quick_result(session_id, "DETECTING", self._now())
        if self._background:
            threading.Thread(target=self.run, args=(session_id,), daemon=True, name=f"quick-{session_id}").start()
        else:
            self.run(session_id)

    def prefetch(self, session_id: int) -> None:
        """찍은 사진을 뒤에서 미리 탐지한다(찍기 응답은 바로 돌아간다). 실패해도 완료 때 다시 돌리므로 버린다."""
        if not self.predetect:
            return
        if self._background:
            threading.Thread(target=self._prefetch, args=(session_id,), daemon=True,
                             name=f"predetect-{session_id}").start()
        else:
            self._prefetch(session_id)

    def lights_for(self, session_id: int) -> frozenset[str] | None:
        """세션의 판정 조명. 전체 검사(FULL)는 KB(L4)·옆면(L1·L2)·SCR1(OFF)까지 세트 사진 전부를 본다."""
        if inspection_profile(self.store.get_session(session_id).shotlist) != PROFILE_QUICK:
            return None
        return self.lights

    def _prefetch(self, session_id: int) -> None:
        try:
            for shot in judged_shots(self.store.shots(session_id), self.lights_for(session_id)):
                with self._lock:  # 한 장씩 잡았다 놓아 완료 탐지가 오래 기다리지 않게
                    if self.store.get_session(session_id).status != "IN_PROGRESS":
                        return  # 이미 완료(판정이 남은 사진을 돌림)
                    done = self._done.setdefault(session_id, {})
                    if shot.local_path in done:
                        continue
                    detector, info = self._get()
                    done[shot.local_path] = detect_shot(shot, detector, info, self._load)
        except Exception as e:  # noqa: BLE001 — 미리 탐지는 덤: 완료 때 같은 사진을 다시 돌려 오류를 화면에 남긴다
            print(f"미리 탐지 실패(완료 때 다시 돌림): {type(e).__name__}: {e}")

    def run(self, session_id: int) -> None:
        try:
            with self._lock:
                detector, info = self._get()
                done = self._done.pop(session_id, {})
                lights = self.lights_for(session_id)
                result = detect_set(self.store.shots(session_id), detector, info, self._load, self._clock,
                                    lights=lights, done=done)
                if not result.photos:  # 판정 조명과 맞는 사진이 없으면 "문제 없음"이 아니라 실패로 남긴다
                    raise DetectError(f"판정할 사진이 없음(판정 조명 {sorted(lights or [])})")
                ahead = sum(1 for p in result.photos if any(p is d for d in done.values()))
        except Exception as e:  # noqa: BLE001 — 백그라운드 스레드: 엔진·사진 실패를 모두 화면에 남겨 다시 돌리게
            self.store.put_quick_result(session_id, "FAILED", self._now(), error=f"{type(e).__name__}: {e}"[:300])
        else:
            verdict = "DEFECT" if result.has_problem else "CLEAN"  # 오염만 잡히면 CLEAN(서버 등급 A와 같게)
            data = result.to_json()
            data["predetected"] = ahead  # 찍는 동안 미리 끝낸 장수(seconds에는 남은 몫만 들어 있다)
            data["lights"] = sorted(lights) if lights else None
            # 엔진 없는 시험·시험용 기본 모델(model.json testOnly)은 화면에만 보이고 서버에 올리지 않는다
            data["testOnly"] = info.model_version == NO_ENGINE_VERSION or bool(getattr(info, "test_only", False))
            self.store.put_quick_result(session_id, "DONE", self._now(), verdict=verdict,
                                        result_json=json.dumps(data, ensure_ascii=False))
        if self.after is not None:
            self.after(session_id)

    def _get(self) -> tuple[Detector, ModelInfo]:
        if self._detector is None:
            self._detector = self._source()
        return self._detector


def result_json(store: Store, session_id: int, shooting: bool) -> dict[str, Any]:
    """`GET /api/sessions/current/result`. 폴리곤은 원본 사진 px이고, 화면은 width·height로 축소 사진에 맞춰 그린다."""
    session = store.get_session(session_id)
    record = store.get_quick_result(session_id)
    out: dict[str, Any] = {
        "sessionId": session.public_id, "deviceId": session.device_id, "profile": inspection_profile(session.shotlist),
        "status": "SHOOTING" if shooting else "WAITING",
        "verdict": None, "count": 0, "faces": {}, "seconds": None, "modelVersion": None, "testOnly": False,
        "error": None, "photos": [],
    }
    if record is None:
        return out
    out.update(status=record.status, verdict=record.verdict, error=record.error)
    if record.result_json is None:
        return out
    data = json.loads(record.result_json)
    by_name: dict[str, ShotRecord] = {s.filename: s for s in store.shots(session_id)}
    faces: dict[str, int] = {}
    photos = []
    for p in data["photos"]:
        faces[p["face"]] = faces.get(p["face"], 0) + len(p["detections"])
        shot = by_name.get(p["filename"])
        photos.append({
            "shotId": shot.public_id if shot else None, "face": p["face"], "light": p["light"], "camera": p["camera"],
            "width": p.get("width", 0), "height": p.get("height", 0), "detections": p["detections"],
        })
    out.update(count=data["count"], faces=faces, seconds=data["seconds"], modelVersion=data["modelVersion"],
               testOnly=bool(data.get("testOnly")) or data["modelVersion"] == NO_ENGINE_VERSION, photos=photos)
    return out
