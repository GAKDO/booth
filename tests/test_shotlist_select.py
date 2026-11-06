"""11/6: 학습 화면에서 촬영 목록(위·아래·펼친·옆면)을 골라 서버를 다시 켜지 않고 찍는다."""

import sqlite3

import pytest

from trace_booth.session import CaptureService, InvalidInput
from trace_booth.shotlist import SHOTLISTS, StepKind
from trace_booth.store import Store


@pytest.fixture
def svc(tmp_path, cams, presets) -> CaptureService:
    return CaptureService(
        Store(tmp_path / "booth.db"), cams, presets, tmp_path / "data", steps=SHOTLISTS["mvp"], min_free_bytes=0,
        training_lists=SHOTLISTS, default_list="mvp",
    )


def faces(svc: CaptureService, sid: int) -> set[str]:
    steps = svc.steps_for(svc.store.get_session(sid))
    return {s.face.value for s in steps if s.kind is StepKind.SHOT}


def test_목록을_고르지_않으면_서버를_켤_때_고른_기본_목록으로_찍는다(svc) -> None:
    s = svc.start("MB05")
    assert s.shotlist == "mvp" and faces(svc, s.id) == {"BOT", "TOP"}


def test_기본_목록을_바꿔_서버를_다시_켜도_예전_세션은_새_목록으로_이어지지_않는다(tmp_path, cams, presets) -> None:
    def server(default: str) -> CaptureService:
        return CaptureService(
            Store(tmp_path / "booth.db"), cams, presets, tmp_path / "data", steps=SHOTLISTS[default],
            min_free_bytes=0, training_lists=SHOTLISTS, default_list=default,
        )

    kb = server("kb").start("MB05")
    side = server("side")
    s = side.start("MB05")
    assert s.id != kb.id and s.shotlist == "side"
    assert faces(side, kb.id) == {"KB"}  # 예전 세션은 자기 목록 그대로


def test_학습에서_kb를_고르면_펼친_목록으로_side를_고르면_옆면_목록으로_찍는다(svc) -> None:
    assert faces(svc, svc.start("MB05", shotlist="kb").id) == {"KB"}
    assert faces(svc, svc.start("MB05", shotlist="side").id) >= {"SF", "SB", "SL", "SR", "SCR0"}


def test_같은_기기를_다른_목록으로_시작하면_새_세션이고_같은_목록이면_이어_찍는다(svc) -> None:
    mvp = svc.start("MB05")
    kb = svc.start("MB05", shotlist="kb")
    assert kb.id != mvp.id
    assert svc.start("MB05", shotlist="kb").id == kb.id
    assert svc.start("MB05").id == mvp.id
    assert svc.start("MB05", shotlist="mvp").id == mvp.id  # 기본 목록 이름을 줘도 같은 세션


def test_없는_목록은_거절한다(svc) -> None:
    with pytest.raises(InvalidInput):
        svc.start("MB05", shotlist="top")


def test_예전_부스_DB에도_목록_칸을_더하고_예전_세션은_그대로_읽힌다(tmp_path) -> None:
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE session (id INTEGER PRIMARY KEY AUTOINCREMENT, public_id TEXT NOT NULL UNIQUE, "
        "device_id TEXT NOT NULL, mode TEXT NOT NULL, current_step INTEGER NOT NULL DEFAULT 0, scr1_skip_reason TEXT, "
        "status TEXT NOT NULL DEFAULT 'IN_PROGRESS', started_at TEXT NOT NULL, completed_at TEXT)"
    )
    conn.execute("INSERT INTO session (public_id, device_id, mode, started_at) VALUES ('x', 'MB02', 'TRAINING', 't')")
    conn.commit()
    conn.close()
    store = Store(db)
    old = store.find_open_session("MB02", "TRAINING")
    assert old is not None and old.shotlist is None
    Store(db)  # 두 번 열어도 칸을 다시 더하지 않는다


def test_화면은_학습_목록과_기본을_받고_고른_목록으로_세션을_연다(svc) -> None:
    import json
    import threading
    import urllib.request

    from trace_booth.web import App, make_server

    server = make_server(App(svc), "127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with urllib.request.urlopen(base + "/api/shotlists") as r:
            data = json.loads(r.read())
        assert data["default"] == "mvp"
        assert {"name": "kb", "label": "펼친 상태(키보드)", "photos": 6} in data["lists"]
        req = urllib.request.Request(
            base + "/api/sessions", method="POST", headers={"Content-Type": "application/json"},
            data=json.dumps({"deviceId": "MB07", "mode": "TRAINING", "shotlist": "side"}).encode(),
        )
        with urllib.request.urlopen(req) as r:
            state = json.loads(r.read())
        assert state["shotlist"] == "side"
    finally:
        server.shutdown()
        server.server_close()
