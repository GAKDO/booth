"""이 파일이 확인하는 것: 촬영 목록 고르기(11/6). 학습 데이터 화면에서 목록(위·아래 mvp, 펼친 키보드 kb, 옆면 side)을
골라 부스 서버를 다시 켜지 않고 찍을 수 있는지, 목록이 다른 촬영이 섞이지 않는지 본다.

11/6: 학습 화면에서 촬영 목록(위·아래·펼친·옆면)을 골라 서버를 다시 켜지 않고 찍는다.
"""

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


# 테스트 도구: 세션의 촬영 목록에 들어 있는 면 이름을 모은다.
def faces(svc: CaptureService, sid: int) -> set[str]:
    steps = svc.steps_for(svc.store.get_session(sid))
    return {s.face.value for s in steps if s.kind is StepKind.SHOT}


# [상황] 목록을 고르지 않고 촬영을 시작한다.
# [기대] 서버 기본 목록(mvp: 바닥·윗면)으로 찍는다.
def test_목록을_고르지_않으면_서버를_켤_때_고른_기본_목록으로_찍는다(svc) -> None:
    s = svc.start("MB05")
    assert s.shotlist == "mvp" and faces(svc, s.id) == {"BOT", "TOP"}


# [상황] 키보드 목록으로 찍던 중 서버 기본 목록을 옆면으로 바꿔 다시 켠다.
# [기대] 새 촬영은 옆면 목록의 새 세션이고, 예전 세션은 자기 목록(키보드) 그대로다.
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


# [상황] 학습 화면에서 kb, side를 각각 고른다.
# [기대] kb는 키보드 면만, side는 네 옆면과 꺼진 화면을 찍는다.
def test_학습에서_kb를_고르면_펼친_목록으로_side를_고르면_옆면_목록으로_찍는다(svc) -> None:
    assert faces(svc, svc.start("MB05", shotlist="kb").id) == {"KB"}
    assert faces(svc, svc.start("MB05", shotlist="side").id) >= {"SF", "SB", "SL", "SR", "SCR0"}


# [상황] 같은 기기를 mvp로 찍다가 kb로 시작하고, 다시 각각의 목록으로 시작한다.
# [기대] 목록이 다르면 새 세션, 같으면 하던 세션을 이어 찍는다(기본 목록 이름을 직접 줘도 같음).
def test_같은_기기를_다른_목록으로_시작하면_새_세션이고_같은_목록이면_이어_찍는다(svc) -> None:
    mvp = svc.start("MB05")
    kb = svc.start("MB05", shotlist="kb")
    assert kb.id != mvp.id
    assert svc.start("MB05", shotlist="kb").id == kb.id
    assert svc.start("MB05").id == mvp.id
    assert svc.start("MB05", shotlist="mvp").id == mvp.id  # 기본 목록 이름을 줘도 같은 세션


# [상황] 없는 목록 이름으로 시작한다.
# [기대] 잘못된 입력으로 거절한다.
def test_없는_목록은_거절한다(svc) -> None:
    with pytest.raises(InvalidInput):
        svc.start("MB05", shotlist="top")


# [상황] 목록 칸이 없던 예전 부스 DB 파일을 연다.
# [기대] 칸을 더해 주고, 예전 세션은 목록 없음으로 그대로 읽힌다. 두 번 열어도 칸을 또 더하지 않는다.
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


# [상황] 부스 화면이 목록 정보를 받고, 옆면 목록으로 세션을 연다.
# [기대] 기본(mvp)과 목록마다 이름·설명·장수(예: 펼친 상태 6장)를 받고, 고른 목록으로 세션이 열린다.
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
