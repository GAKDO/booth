"""출고·반납 세트 올리기: 로컬에 남긴 사진 중 면·조명·카메라마다 마지막 장을 골라 capture-server 세트로 올린다.

사진은 이미 로컬 디스크·기록에 있으므로 실패해도 잃지 않는다. 실패하면 같은 함수를 다시 부르면 된다.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .capture_client import ApiError, CaptureClient, HttpCall, PhotoSpec, http_call, put_photo
from .store import ShotRecord

PUT_TRIES = 3


class UploadFailed(Exception):
    """사진을 올리지 못함. 세트는 완료되지 않았고, 다시 시도하면 새 세트를 만든다."""


class _UrlExpired(Exception):
    pass


@dataclass
class SubmitResult:
    capture_set_id: str
    photos: int
    status: str  # complete 응답의 status(UPLOADED 등)
    new_sets: int  # URL 만료로 세트를 다시 만든 횟수


def select_photos(shots: list[ShotRecord]) -> list[PhotoSpec]:
    """SLATE를 빼고 (면, 조명, 카메라)마다 번호가 가장 큰(마지막에 다시 찍은) 사진 하나. 찍은 차례대로."""
    latest: dict[tuple[str, str, str], ShotRecord] = {}
    for s in shots:
        if s.kind != "SHOT" or s.face is None or s.light is None:
            continue
        key = (s.face, s.light, s.camera)
        if key not in latest or s.seq > latest[key].seq:
            latest[key] = s
    chosen = sorted(latest.values(), key=lambda s: (s.captured_at, s.id or 0))
    return [PhotoSpec(s.face or "", s.light or "", s.camera, s.sha256, s.captured_at, Path(s.local_path)) for s in chosen]


def _put_all(
    client_http: HttpCall, uploads: list, photos: list[PhotoSpec], sleep: Callable[[float], None]
) -> None:
    for target, photo in zip(uploads, photos):
        for attempt in range(1, PUT_TRIES + 1):
            try:
                status = put_photo(target, photo, client_http)
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


def submit(
    shots: list[ShotRecord],
    rental_id: str,
    checkpoint_id: str,
    skipped_faces: dict[str, str],
    client: CaptureClient,
    http: HttpCall = http_call,
    sleep: Callable[[float], None] = time.sleep,
) -> SubmitResult:
    photos = select_photos(shots)
    if not photos:
        raise UploadFailed("올릴 사진이 없음")
    new_sets = 0
    while True:
        set_id, uploads = client.create_set(rental_id, checkpoint_id, photos)
        try:
            _put_all(http, uploads, photos, sleep)
            break
        except _UrlExpired:
            # 계약에 URL을 다시 받는 API가 없어 세트를 새로 만든다(이전 세트는 완료하지 않음).
            if new_sets >= 1:
                raise UploadFailed("사전 서명 URL이 두 번 만료됨") from None
            new_sets += 1
    try:
        done = client.complete(set_id, skipped_faces, f"booth-{set_id}-complete")
    except ApiError as e:
        raise UploadFailed(f"완료 실패: {e}") from e
    return SubmitResult(set_id, len(photos), str(done.get("status", "")), new_sets)
