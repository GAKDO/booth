"""출고·반납 세트 올리기: 로컬에 남긴 사진 중 면·조명·카메라마다 마지막 장을 골라 capture-server 세트로 올린다.

사진은 이미 로컬 디스크·기록에 있으므로 실패해도 잃지 않는다. 실패하면 같은 함수를 다시 부르면 된다.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .capture_client import ApiError, CaptureClient, HttpCall, PhotoSpec, UploadTarget, http_call, put_photo
from .store import CheckoutRecord, ShotRecord, Store, latest_shots

PUT_TRIES = 3
MAX_REISSUE = 2  # URL 다시 받기 최대 횟수


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


def select_photos(shots: list[ShotRecord]) -> list[PhotoSpec]:
    """SLATE를 빼고 (면, 조명, 카메라)마다 마지막에 찍은 사진 하나. 찍은 차례대로."""
    return [PhotoSpec(s.face or "", s.light or "", s.camera, s.sha256, s.captured_at, Path(s.local_path))
            for s in latest_shots(shots)]


def _put_all(http: HttpCall, pending: list[tuple[UploadTarget, PhotoSpec]], sleep: Callable[[float], None]) -> None:
    """올린 사진은 pending에서 뺀다. URL이 만료되면 남은 것을 둔 채 _UrlExpired."""
    while pending:
        target, photo = pending[0]
        for attempt in range(1, PUT_TRIES + 1):
            try:
                status = put_photo(target, photo, http)
            except OSError as e:
                status, error = 0, type(e).__name__
            else:
                error = f"HTTP {status}"
            if 200 <= status < 300:
                break
            if status == 403:
                raise _UrlExpired  # MinIO는 서명이 만료되면 403 AccessDenied
            if attempt == PUT_TRIES:
                raise UploadFailed(f"{photo.face}·{photo.light}·{photo.camera} 올리기 실패: {error}")
            sleep(2.0 * attempt)
        pending.pop(0)


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
) -> SubmitResult:
    photos = select_photos(shots)
    if not photos:
        raise UploadFailed("올릴 사진이 없음")
    set_id, uploads = client.create_set(rental_id, checkpoint_id, photos, profile)
    pending = list(zip(uploads, photos))
    reissued = 0
    while True:
        try:
            _put_all(http, pending, sleep)
            break
        except _UrlExpired:
            # 세트는 그대로 두고 남은 사진의 URL만 다시 받는다(contracts 11/2 결정, POST .../uploads).
            if reissued >= MAX_REISSUE:
                raise UploadFailed(f"사전 서명 URL을 {MAX_REISSUE}번 다시 받아도 만료됨") from None
            reissued += 1
            _reissue(client, set_id, pending)
    try:
        done = client.complete(set_id, skipped_faces, f"booth-{set_id}-complete")
    except ApiError as e:
        raise UploadFailed(f"완료 실패: {e}") from e
    return SubmitResult(set_id, len(photos), str(done.get("status", "")), reissued)


def submit_session(
    store: Store,
    session_id: int,
    client: CaptureClient,
    now: Callable[[], str],
    http: HttpCall = http_call,
    sleep: Callable[[float], None] = time.sleep,
    profile: str | None = None,
) -> CheckoutRecord:
    """끝난 출고·반납 세션을 올리고 결과를 기록한다. 실패해도 사진은 로컬에 남고, 다시 부르면 처음부터 다시 올린다."""
    session = store.get_session(session_id)
    record = store.get_checkout(session_id)
    if record is None or session.status != "COMPLETE":
        raise UploadFailed("완료된 출고·반납 촬영이 아님")
    if record.status == "SUBMITTED":
        return record
    skipped = {"SCR1": session.scr1_skip_reason} if session.scr1_skip_reason else {}
    try:
        result = submit(store.shots(session_id), record.rental_id, record.checkpoint_id, skipped, client, http, sleep,
                        profile)
    except (UploadFailed, ApiError, OSError) as e:
        store.mark_checkout(session_id, "FAILED", now(), error=f"{type(e).__name__}: {e}"[:300])
    else:
        store.mark_checkout(session_id, "SUBMITTED", now(), capture_set_id=result.capture_set_id)
    return store.get_checkout(session_id) or record
