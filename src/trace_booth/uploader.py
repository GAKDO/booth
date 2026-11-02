"""학습 데이터 모드 업로드: 로컬에 남긴 사진을 trace-raw로 올리고, 실패하면 다시 올린다(FR-DAT-02)."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from . import __version__
from .store import ShotRecord, Store

MAX_BACKOFF_SECONDS = 60


class ObjectStore(Protocol):
    def head(self, key: str) -> dict[str, str] | None:
        """객체가 있으면 메타데이터, 없으면 None."""
        ...

    def put(self, key: str, path: Path, metadata: dict[str, str]) -> None: ...


class S3ObjectStore:
    """MinIO(S3 API). 접속 정보는 gitops와 같은 환경 변수 이름(MINIO_ENDPOINT 등)으로 받는다."""

    def __init__(self, endpoint: str, access_key: str, secret_key: str, bucket: str) -> None:
        import boto3

        self.bucket = bucket
        self._client = boto3.client(
            "s3", endpoint_url=endpoint, aws_access_key_id=access_key, aws_secret_access_key=secret_key
        )

    def head(self, key: str) -> dict[str, str] | None:
        from botocore.exceptions import ClientError

        try:
            return dict(self._client.head_object(Bucket=self.bucket, Key=key).get("Metadata", {}))
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return None
            raise

    def put(self, key: str, path: Path, metadata: dict[str, str]) -> None:
        self._client.upload_file(
            str(path), self.bucket, key, ExtraArgs={"Metadata": metadata, "ContentType": "image/jpeg"}
        )


def object_key(shot: ShotRecord) -> str:
    """contracts/storage-layout.md: `{기기ID}/{파일명}`, SLATE는 `{기기ID}/slate/{파일명}`."""
    device_id = shot.filename.split("_", 1)[0]
    if shot.kind == "SLATE":
        return f"{device_id}/slate/{shot.filename}"
    return f"{device_id}/{shot.filename}"


def object_metadata(shot: ShotRecord) -> dict[str, str]:
    meta = {
        "sha256": shot.sha256,
        "camera": shot.camera,
        "capturedat": shot.captured_at,
        "toolversion": __version__,
        "camerasettings": shot.camera_settings,
    }
    if shot.face:
        meta["face"] = shot.face
    if shot.light:
        meta["light"] = shot.light
    return meta


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class UploadWorker:
    def __init__(
        self,
        store: Store,
        objects: ObjectStore,
        now: Callable[[], str] = _now,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.store = store
        self.objects = objects
        self._now = now
        self._sleep = sleep

    def upload(self, shot: ShotRecord) -> str:
        """한 장 올리고 상태(UPLOADED·CONFLICT·FAILED)를 기록해 돌려준다. 같은 키를 덮어쓰지 않는다."""
        key = object_key(shot)
        try:
            existing = self.objects.head(key)
            if existing is not None:
                status = "UPLOADED" if existing.get("sha256") == shot.sha256 else "CONFLICT"
                error = None if status == "UPLOADED" else "같은 키에 다른 해시의 객체가 있음"
            else:
                self.objects.put(key, Path(shot.local_path), object_metadata(shot))
                status, error = "UPLOADED", None
        except Exception as e:  # noqa: BLE001 - 네트워크·인증·저장소 오류는 모두 다시 시도할 대상
            status, error = "FAILED", f"{type(e).__name__}: {e}"[:300]
        self.store.mark_upload(shot.id, status, self._now(), error)
        return status

    def run_once(self) -> tuple[int, int]:
        """대기열을 한 번 비운다. (올린 수, 실패 수)."""
        uploaded = failed = 0
        for shot in self.store.pending_uploads():
            status = self.upload(shot)
            if status == "FAILED":
                failed += 1
                break  # 저장소에 닿지 않으면 나머지도 실패할 것이므로 쉬었다가 다시
            uploaded += status == "UPLOADED"
        return uploaded, failed

    def run_forever(self, stop: threading.Event, idle_seconds: float = 2.0) -> None:
        backoff = 2.0
        while not stop.is_set():
            _, failed = self.run_once()
            if failed:
                self._sleep(backoff)
                backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
            else:
                backoff = 2.0
                self._sleep(idle_seconds)

    def start(self) -> tuple[threading.Thread, threading.Event]:
        stop = threading.Event()
        thread = threading.Thread(target=self.run_forever, args=(stop,), name="trace-upload", daemon=True)
        thread.start()
        return thread, stop
