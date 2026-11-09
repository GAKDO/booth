"""출고·반납 세트 올리기: 로컬에 남긴 사진 중 면·조명·카메라마다 마지막 장을 골라 capture-server 세트로 올린다.

사진은 이미 로컬 디스크·기록에 있으므로 실패해도 잃지 않는다. 실패하면 같은 함수를 다시 부르면 된다.
사진은 UPLOAD_WORKERS장씩 동시에 올린다(TRACE_BOOTH_UPLOAD_WORKERS, 1이면 예전처럼 한 장씩). 원본 그대로 올린다:
sha256·폴리곤 좌표가 원본 기준이라 줄이거나 다시 압축하지 않는다.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from .capture_client import ApiError, CaptureClient, HttpCall, PhotoSpec, UploadTarget, http_call, put_photo
from .store import CheckoutRecord, ShotRecord, Store, latest_shots

PUT_TRIES = 3
MAX_REISSUE = 2  # URL 다시 받기 최대 횟수
UPLOAD_WORKERS = 4  # 동시에 올리는 사진 수(기본)


def upload_workers() -> int:
    """TRACE_BOOTH_UPLOAD_WORKERS(1~8). 없거나 이상하면 UPLOAD_WORKERS."""
    raw = os.environ.get("TRACE_BOOTH_UPLOAD_WORKERS", "").strip()
    return max(1, min(8, int(raw))) if raw.isdigit() else UPLOAD_WORKERS


class UploadFailed(Exception):
    """사진을 올리지 못함. 세트는 완료되지 않았고, 다시 시도하면 새 세트를 만든다."""


class _UrlExpired(Exception):
    pass


@dataclass
class SubmitResult:
    capture_set_id: str
    photos: int
    status: str  # complete 응답의 status(UPLOADED 등)
    reissued: int  # URL 만료로 남은 사진의 URL을 다시 받은 횟수
    put_seconds: float = 0.0  # 사진 올리기(PUT)에 걸린 시간
    put_bytes: int = 0  # 이번에 올린 사진 바이트


def select_photos(shots: list[ShotRecord]) -> list[PhotoSpec]:
    """SLATE를 빼고 (면, 조명, 카메라)마다 마지막에 찍은 사진 하나. 찍은 차례대로."""
    return [PhotoSpec(s.face or "", s.light or "", s.camera, s.sha256, s.captured_at, Path(s.local_path))
            for s in latest_shots(shots)]


def _put_one(http: HttpCall, target: UploadTarget, photo: PhotoSpec, sleep: Callable[[float], None]) -> bool:
    """한 장 올린다. 올렸으면 True, URL이 만료됐으면 False. 계속 실패하면 UploadFailed."""
    for attempt in range(1, PUT_TRIES + 1):
        try:
            status = put_photo(target, photo, http)
        except OSError as e:
            status, error = 0, type(e).__name__
        else:
            error = f"HTTP {status}"
        if 200 <= status < 300:
            return True
        if status == 403:
            return False  # MinIO는 서명이 만료되면 403 AccessDenied
        if attempt == PUT_TRIES:
            raise UploadFailed(f"{photo.face}·{photo.light}·{photo.camera} 올리기 실패: {error}")
        sleep(2.0 * attempt)
    raise AssertionError("도달하지 않음")


def _put_all(
    http: HttpCall, pending: list[tuple[UploadTarget, PhotoSpec]], sleep: Callable[[float], None], workers: int = 1,
) -> None:
    """올린 사진은 pending에서 뺀다. URL이 만료되면 남은 것을 둔 채 _UrlExpired.

    workers > 1이면 여러 장을 동시에 올린다. 한 장이 실패해도 나머지는 끝까지 올려 두고(다음 시도는 남은 것만) 실패를 알린다."""
    if workers <= 1 or len(pending) <= 1:
        while pending:
            target, photo = pending[0]
            if not _put_one(http, target, photo, sleep):
                raise _UrlExpired
            pending.pop(0)
        return
    with ThreadPoolExecutor(max_workers=min(workers, len(pending)), thread_name_prefix="put") as pool:
        futures = [pool.submit(_put_one, http, t, p, sleep) for t, p in pending]
    outcomes: list[bool | UploadFailed] = []
    for f in futures:
        try:
            outcomes.append(f.result())
        except UploadFailed as e:
            outcomes.append(e)
    pending[:] = [item for item, ok in zip(pending, outcomes) if ok is not True]
    if any(ok is False for ok in outcomes):
        raise _UrlExpired
    failed = next((ok for ok in outcomes if isinstance(ok, UploadFailed)), None)
    if failed is not None:
        raise failed


def _size(photo: PhotoSpec) -> int:
    try:
        return photo.path.stat().st_size
    except OSError:
        return 0


def _reissue(client: CaptureClient, set_id: str, pending: list[tuple[UploadTarget, PhotoSpec]]) -> None:
    """남은 사진의 URL을 다시 받아 pending을 바꾼다. 서버가 이미 받았다고 빼 준 사진은 pending에서도 뺀다."""
    targets = {(t.face, t.light, t.camera): t for t in client.reissue_uploads(set_id, [p for _, p in pending])}
    pending[:] = [(targets[k], p) for _, p in pending if (k := (p.face, p.light, p.camera)) in targets]


def submit(
    shots: list[ShotRecord],
    rental_id: str,
    checkpoint_id: str,
    skipped_faces: dict[str, str],
    client: CaptureClient,
    http: HttpCall = http_call,
    sleep: Callable[[float], None] = time.sleep,
    profile: str | None = None,
    existing_set_id: str | None = None,
    on_created: Callable[[str], None] | None = None,
    workers: int | None = None,
) -> SubmitResult:
    """existing_set_id가 있으면(지난번에 만들고 못 끝낸 세트) 새로 만들지 않고 남은 사진의 URL만 다시 받아 이어 간다."""
    photos = select_photos(shots)
    if not photos:
        raise UploadFailed("올릴 사진이 없음")
    if existing_set_id is None:
        set_id, uploads = client.create_set(rental_id, checkpoint_id, photos, profile)
        if on_created is not None:
            on_created(set_id)  # 사진 올리기에서 실패해도 다음엔 이 세트를 이어 쓰게
        pending = list(zip(uploads, photos))
    else:
        set_id = existing_set_id
        pending = [(UploadTarget("", "", "", "", {}, ""), p) for p in photos]
        try:
            _reissue(client, set_id, pending)
        except ApiError as e:
            if e.code != "CAPTURE_SET_CLOSED":
                raise
            pending = []  # 사진은 다 올라가 완료까지 갔던 세트: 같은 키로 완료를 다시 불러 결과만 받는다
    reissued = 0
    workers = upload_workers() if workers is None else workers
    started = time.perf_counter()
    sent = sum(_size(p) for _, p in pending)  # 끝까지 가면 이만큼 올라간다(실패하면 예외로 빠져나감)
    while True:
        try:
            _put_all(http, pending, sleep, workers)
            break
        except _UrlExpired:
            # 세트는 그대로 두고 남은 사진의 URL만 다시 받는다(contracts 11/2 결정, POST .../uploads).
            if reissued >= MAX_REISSUE:
                raise UploadFailed(f"사전 서명 URL을 {MAX_REISSUE}번 다시 받아도 만료됨") from None
            reissued += 1
            _reissue(client, set_id, pending)
    put_seconds = time.perf_counter() - started
    if sent:
        print(f"사진 올리기 {sent / 1e6:.1f}MB, {put_seconds:.1f}초 "
              f"({sent / 1e6 / max(put_seconds, 1e-3):.2f}MB/s, 동시 {workers}장)")
    try:
        done = client.complete(set_id, skipped_faces, f"booth-{set_id}-complete")
    except ApiError as e:
        raise UploadFailed(f"완료 실패: {e}") from e
    return SubmitResult(set_id, len(photos), str(done.get("status", "")), reissued, put_seconds, sent)


def submit_session(
    store: Store,
    session_id: int,
    client: CaptureClient,
    now: Callable[[], str],
    http: HttpCall = http_call,
    sleep: Callable[[float], None] = time.sleep,
    profile: str | None = None,
) -> CheckoutRecord:
    """끝난 출고·반납·빠른 검사 세션을 올리고 결과를 기록한다. 실패해도 사진은 로컬에 남고, 다시 부르면
    만들어 둔 세트를 이어 간다(남은 사진만)."""
    session = store.get_session(session_id)
    record = store.get_checkout(session_id)
    if record is None or session.status != "COMPLETE":
        raise UploadFailed("완료된 출고·반납 촬영이 아님")
    if record.status == "SUBMITTED":
        return record
    skipped = {"SCR1": session.scr1_skip_reason} if session.scr1_skip_reason else {}
    try:
        result = submit(store.shots(session_id), record.rental_id, record.checkpoint_id, skipped, client, http, sleep,
                        profile, record.capture_set_id, lambda sid: store.set_checkout_set_id(session_id, sid))
    except (UploadFailed, ApiError, OSError) as e:
        store.mark_checkout(session_id, "FAILED", now(), error=f"{type(e).__name__}: {e}"[:300])
    else:
        store.mark_checkout(session_id, "SUBMITTED", now(), capture_set_id=result.capture_set_id)
    return store.get_checkout(session_id) or record
