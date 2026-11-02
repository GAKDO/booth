"""로컬 촬영 기록(SQLite). 행을 지우는 함수는 두지 않는다(헌법 III)."""

from __future__ import annotations

import os
import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path

from nanoid import generate

_SCHEMA = """
CREATE TABLE IF NOT EXISTS session (
  id INTEGER PRIMARY KEY AUTOINCREMENT, public_id TEXT NOT NULL UNIQUE, device_id TEXT NOT NULL, mode TEXT NOT NULL,
  current_step INTEGER NOT NULL DEFAULT 0, scr1_skip_reason TEXT,
  status TEXT NOT NULL DEFAULT 'IN_PROGRESS', started_at TEXT NOT NULL, completed_at TEXT
);
CREATE TABLE IF NOT EXISTS shot (
  id INTEGER PRIMARY KEY AUTOINCREMENT, public_id TEXT NOT NULL UNIQUE,
  session_id INTEGER NOT NULL REFERENCES session(id), step_index INTEGER NOT NULL,
  kind TEXT NOT NULL, face TEXT, light TEXT, camera TEXT NOT NULL, seq INTEGER NOT NULL,
  filename TEXT NOT NULL UNIQUE, local_path TEXT NOT NULL, sha256 TEXT NOT NULL, size_bytes INTEGER NOT NULL,
  camera_settings TEXT NOT NULL, captured_at TEXT NOT NULL,
  upload_status TEXT NOT NULL DEFAULT 'PENDING', upload_attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT, uploaded_at TEXT
);
CREATE TABLE IF NOT EXISTS checkout (
  session_id INTEGER PRIMARY KEY REFERENCES session(id), rental_id TEXT NOT NULL, checkpoint_id TEXT NOT NULL,
  capture_set_id TEXT, status TEXT NOT NULL DEFAULT 'PENDING', attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT, updated_at TEXT
);
"""


def new_public_id() -> str:
    """밖에 보이는 ID: NanoID 21자(헌법 1.1.0). 내부 기본 키 id(정수)는 화면·API에 내보내지 않는다."""
    return generate()


@dataclass
class SessionRecord:
    id: int
    public_id: str
    device_id: str
    mode: str
    current_step: int
    scr1_skip_reason: str | None
    status: str
    started_at: str
    completed_at: str | None


@dataclass
class ShotRecord:
    session_id: int
    step_index: int
    kind: str
    face: str | None
    light: str | None
    camera: str
    seq: int
    filename: str
    local_path: str
    sha256: str
    size_bytes: int
    camera_settings: str
    captured_at: str
    public_id: str = field(default_factory=new_public_id)
    id: int | None = None  # 넣을 때 SQLite가 매긴다
    upload_status: str = "PENDING"
    upload_attempts: int = 0
    last_error: str | None = None
    uploaded_at: str | None = None


def latest_shots(shots: list[ShotRecord]) -> list[ShotRecord]:
    """면 사진만(SLATE 제외) (면, 조명, 카메라)마다 번호가 가장 큰(마지막에 다시 찍은) 한 장. 찍은 차례대로.

    출고·반납 세트에 올리는 사진과 탐지하는 사진이 같아야 하므로 둘 다 이 함수로 고른다.
    """
    latest: dict[tuple[str, str, str], ShotRecord] = {}
    for s in shots:
        if s.kind != "SHOT" or s.face is None or s.light is None:
            continue
        key = (s.face, s.light, s.camera)
        if key not in latest or s.seq > latest[key].seq:
            latest[key] = s
    return sorted(latest.values(), key=lambda s: (s.captured_at, s.id or 0))


@dataclass
class CheckoutRecord:
    """출고·반납 세션이 올릴 렌탈·시점(관리자 화면이 고른 publicId)과 올리기 결과."""

    session_id: int
    rental_id: str
    checkpoint_id: str
    capture_set_id: str | None = None
    status: str = "PENDING"  # PENDING·SUBMITTED·FAILED
    attempts: int = 0
    last_error: str | None = None
    updated_at: str | None = None


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(os.fspath(path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(_SCHEMA)

    def create_session(self, device_id: str, mode: str, now: str) -> SessionRecord:
        public_id = new_public_id()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO session (public_id, device_id, mode, started_at) VALUES (?, ?, ?, ?)",
                (public_id, device_id, mode, now),
            )
        return SessionRecord(int(cur.lastrowid or 0), public_id, device_id, mode, 0, None, "IN_PROGRESS", now, None)

    def find_open_session(self, device_id: str, mode: str) -> SessionRecord | None:
        row = self._one(
            "SELECT * FROM session WHERE device_id = ? AND mode = ? AND status = 'IN_PROGRESS' "
            "ORDER BY started_at DESC LIMIT 1",
            (device_id, mode),
        )
        return SessionRecord(**dict(row)) if row else None

    def get_session(self, session_id: int) -> SessionRecord:
        row = self._one("SELECT * FROM session WHERE id = ?", (session_id,))
        if row is None:
            raise KeyError(session_id)
        return SessionRecord(**dict(row))

    def find_session(self, public_id: str) -> SessionRecord | None:
        row = self._one("SELECT * FROM session WHERE public_id = ?", (public_id,))
        return SessionRecord(**dict(row)) if row else None

    def update_session(self, session_id: int, **values: object) -> None:
        allowed = {"current_step", "scr1_skip_reason", "status", "completed_at"}
        if not values or set(values) - allowed:
            raise ValueError(f"고칠 수 없는 칸: {set(values) - allowed}")
        cols = ", ".join(f"{k} = ?" for k in values)
        with self._lock:
            self._conn.execute(f"UPDATE session SET {cols} WHERE id = ?", (*values.values(), session_id))

    def next_seq(self, device_id: str, kind: str, face: str | None, light: str | None, camera: str) -> int:
        row = self._one(
            "SELECT COALESCE(MAX(s.seq), 0) AS m FROM shot s JOIN session e ON e.id = s.session_id "
            "WHERE e.device_id = ? AND s.kind = ? AND s.face IS ? AND s.light IS ? AND s.camera = ?",
            (device_id, kind, face, light, camera),
        )
        return int(row["m"]) + 1

    def add_shot(self, shot: ShotRecord) -> None:
        cols = [c for c in shot.__dataclass_fields__ if c != "id"]
        with self._lock:
            cur = self._conn.execute(
                f"INSERT INTO shot ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
                tuple(getattr(shot, c) for c in cols),
            )
        shot.id = int(cur.lastrowid or 0)

    def shots(self, session_id: int) -> list[ShotRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM shot WHERE session_id = ? ORDER BY captured_at, rowid", (session_id,)
            ).fetchall()
        return [ShotRecord(**dict(r)) for r in rows]

    def get_shot(self, public_id: str) -> ShotRecord | None:
        row = self._one("SELECT * FROM shot WHERE public_id = ?", (public_id,))
        return ShotRecord(**dict(row)) if row else None

    def pending_uploads(self, limit: int = 20) -> list[ShotRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT s.* FROM shot s JOIN session e ON e.id = s.session_id "
                "WHERE e.mode = 'TRAINING' AND s.upload_status IN ('PENDING', 'FAILED') "
                "ORDER BY s.captured_at, s.rowid LIMIT ?",
                (limit,),
            ).fetchall()
        return [ShotRecord(**dict(r)) for r in rows]

    def mark_upload(self, shot_id: int, status: str, now: str, error: str | None = None) -> None:
        if status not in ("UPLOADED", "FAILED", "CONFLICT"):
            raise ValueError(status)
        with self._lock:
            self._conn.execute(
                "UPDATE shot SET upload_status = ?, upload_attempts = upload_attempts + 1, last_error = ?, "
                "uploaded_at = CASE WHEN ? = 'UPLOADED' THEN ? ELSE uploaded_at END WHERE id = ?",
                (status, error, status, now, shot_id),
            )

    def add_checkout(self, session_id: int, rental_id: str, checkpoint_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO checkout (session_id, rental_id, checkpoint_id) VALUES (?, ?, ?)",
                (session_id, rental_id, checkpoint_id),
            )

    def get_checkout(self, session_id: int) -> CheckoutRecord | None:
        row = self._one("SELECT * FROM checkout WHERE session_id = ?", (session_id,))
        return CheckoutRecord(**dict(row)) if row else None

    def mark_checkout(
        self, session_id: int, status: str, now: str, capture_set_id: str | None = None, error: str | None = None
    ) -> None:
        if status not in ("SUBMITTED", "FAILED"):
            raise ValueError(status)
        with self._lock:
            self._conn.execute(
                "UPDATE checkout SET status = ?, attempts = attempts + 1, last_error = ?, updated_at = ?, "
                "capture_set_id = COALESCE(?, capture_set_id) WHERE session_id = ?",
                (status, error, now, capture_set_id, session_id),
            )

    def shots_for_device(self, device_id: str) -> list[ShotRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT s.* FROM shot s JOIN session e ON e.id = s.session_id WHERE e.device_id = ? "
                "ORDER BY s.captured_at, s.rowid",
                (device_id,),
            ).fetchall()
        return [ShotRecord(**dict(r)) for r in rows]

    def upload_counts(self) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT s.upload_status, COUNT(*) AS n FROM shot s JOIN session e ON e.id = s.session_id "
                "WHERE e.mode = 'TRAINING' GROUP BY s.upload_status"
            ).fetchall()
        counts = {"pending": 0, "failed": 0, "conflict": 0}
        for r in rows:
            key = r["upload_status"].lower()
            if key in counts:
                counts[key] = r["n"]
        return counts

    def _one(self, sql: str, params: tuple) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()
