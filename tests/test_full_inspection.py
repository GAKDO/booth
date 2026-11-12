"""전체 검사(FULL, 11/8) 수용 시나리오: 검사 범위 고르기 → 위·아래·펼친 KB·옆면 28장 → 전체 사진 탐지 → FULL 세트로 올리기.

기본은 지금처럼 빠른 검사(QUICK)여야 하고, 가짜 카메라·가짜 탐지기·가짜 capture-server로 돈다.
"""

from __future__ import annotations

import itertools
import json
import threading
from pathlib import Path

import pytest
from conftest import run_to_end
from fake_capture import BASE, SECRET, FakeCapture
from fakes import FakeCamera
from test_quick_mode import CHECKPOINT, DEVICE, RENTAL, _inspector
from test_web import call

from trace_booth.__main__ import _inspection_profile
from trace_booth.camera_presets import Presets
from trace_booth.capture_client import BoothAuth, CaptureClient
from trace_booth.codes import FACE_REQUIRED_BOOTH, Camera, Face, Light
from trace_booth.quick import result_json
from trace_booth.quick_upload import upload_json, upload_quick
from trace_booth.session import CaptureService, Incomplete, InvalidInput
from trace_booth.shotlist import (
    FULL_INSPECTION_SHOTLIST,
    QUICK_SHOTLIST,
    SIDE_FACES,
    StepKind,
    full_inspection_shotlist,
    required_photo_count,
)
from trace_booth.store import Store
from trace_booth.web import App, make_server

SCR1_REASON = "화면 흰색을 띄울 수 없음(잠김)"


def _shoot_full(service, skip_scr1: str | None = SCR1_REASON):
    s = service.start(DEVICE, "QUICK", RENTAL, CHECKPOINT, profile="FULL")
    run_to_end(service, s.id, skip_scr1=skip_scr1)
    return service.complete(s.id)


def _photos(service, session_id):
    return [s for s in service.store.shots(session_id) if s.kind == "SHOT"]


def test_전체_검사_목록은_ID_카드_없이_필수_28장이고_SCR1만_선택이다():
    steps = FULL_INSPECTION_SHOTLIST
    shots = [s for s in steps if s.kind is StepKind.SHOT]
    assert all(s.kind is not StepKind.SLATE for s in steps)
    assert required_photo_count(steps) == 28
    assert [s.face for s in shots if s.optional] == [Face.SCR1]
    assert {s.face for s in shots if not s.optional} == set(FACE_REQUIRED_BOOTH)


def test_전체_검사는_빠른_검사와_같은_BOT_TOP_12장_뒤에_펼친_KB와_옆면을_찍는다():
    shots = [s for s in FULL_INSPECTION_SHOTLIST if s.kind is StepKind.SHOT]
    quick = [s for s in QUICK_SHOTLIST if s.kind is StepKind.SHOT]
    assert [(s.face, s.light, s.cameras) for s in shots[:6]] == [(s.face, s.light, s.cameras) for s in quick]
    kb = [s for s in shots if s.face is Face.KB]
    assert [s.light for s in kb] == [Light.L1, Light.L2, Light.L4]
    assert all(s.cameras == (Camera.CAM1, Camera.CAM2) for s in kb)
    assert all("펼쳐" in s.instruction and "100°" in s.instruction for s in kb)
    side = [s for s in shots if s.face in SIDE_FACES]
    assert all(s.cameras == (Camera.CAM1,) for s in side)
    assert {s.light for s in side if s.face is not Face.SCR1} == {Light.L1, Light.L2}
    assert all("닫은 채" in s.instruction for s in side if s.face in (Face.SF, Face.SB, Face.SL, Face.SR))
    faces = [s.face for s in shots]
    assert faces.index(Face.KB) > faces.index(Face.TOP) and min(faces.index(f) for f in SIDE_FACES) > faces.index(Face.KB)


def test_전체_검사는_펼치기_전에_바_2를_앞으로_옮기고_옆면_전에_뒤쪽으로_되돌리라고_안내한다():
    for side_camera in (False, True):
        steps = full_inspection_shotlist(side_camera)
        notices = [s.instruction for s in steps if s.kind is StepKind.NOTICE]
        first_kb = next(s.index for s in steps if s.face is Face.KB)
        assert "L4" in steps[first_kb - 1].instruction and steps[first_kb - 1].kind is StepKind.NOTICE
        first_side = next(s.index for s in steps if s.face in SIDE_FACES)
        before_side = steps[first_side - 1]
        assert before_side.kind is StepKind.NOTICE and "되돌리" in before_side.instruction  # L4 자리가 옆면을 가리지 않게
        assert not any("되돌리" in s.instruction for s in steps[first_side:])
        cam1_down = any("CAM1을 삼각대에서 풀어" in n for n in notices)
        assert cam1_down is (not side_camera)  # 옆면 카메라가 있으면 CAM1을 내리지 않는다


def test_검사_범위를_고르지_않으면_지금처럼_빠른_검사_12장이다(service):
    s = service.start(DEVICE, "QUICK", RENTAL, CHECKPOINT)
    assert s.shotlist is None
    run_to_end(service, s.id)
    done = service.complete(s.id)
    assert len(_photos(service, done.id)) == 12


def test_전체_검사를_고르면_28장을_찍고_SCR1은_사유를_적어_건너뛴다(service):
    done = _shoot_full(service)
    photos = _photos(service, done.id)
    assert done.status == "COMPLETE" and len(photos) == 28
    assert {p.face for p in photos} == {f.value for f in FACE_REQUIRED_BOOTH}
    assert done.scr1_skip_reason == SCR1_REASON


def test_전체_검사는_SCR1을_찍어도_되고_그러면_29장이다(service):
    done = _shoot_full(service, skip_scr1=None)
    assert len(_photos(service, done.id)) == 29 and done.scr1_skip_reason is None


def test_전체_검사에서_필수_면이_빠지면_완료하지_않고_빠진_사진을_알린다(service):
    s = service.start(DEVICE, "QUICK", RENTAL, CHECKPOINT, profile="FULL")
    for _ in range(7):  # BOT 3단계 → 뒤집기 안내 → TOP 3단계까지만
        step = service.view(s.id).step
        if step.captures:
            service.shoot(s.id)
        service.advance(s.id)
    with pytest.raises(Incomplete) as e:
        service.complete(s.id)
    assert any(m.startswith("KB_") for m in e.value.missing) and any(m.startswith("SF_") for m in e.value.missing)
    assert not any(m.startswith(("BOT_", "TOP_")) for m in e.value.missing)


def test_서버_기본_범위를_FULL로_켜면_고르지_않은_검사도_전체_검사다(tmp_path, cams, presets):
    service = CaptureService(Store(tmp_path / "b.db"), cams, presets, tmp_path / "d", min_free_bytes=0,
                             default_profile="FULL")
    s = service.start(DEVICE, "QUICK", RENTAL, CHECKPOINT)
    assert service.view(s.id).remaining_steps == len(FULL_INSPECTION_SHOTLIST)
    # 화면에서 빠른 검사를 고르면 그 기기는 빠른 검사로 찍는다
    q = service.start(DEVICE, "QUICK", RENTAL, CHECKPOINT, profile="QUICK")
    assert q.id != s.id and service.view(q.id).remaining_steps == len(QUICK_SHOTLIST)


def test_모르는_검사_범위나_빠진_조명_프리셋이면_시작하지_않는다(tmp_path, cams, service):
    with pytest.raises(InvalidInput):
        service.start(DEVICE, "QUICK", RENTAL, CHECKPOINT, profile="HALF")
    no_l4 = Presets({li: {"exposure_time_absolute": 100} for li in ("L1", "L2", "L3", "OFF")}, "test")
    svc = CaptureService(Store(tmp_path / "b.db"), cams, no_l4, tmp_path / "d", min_free_bytes=0)
    with pytest.raises(InvalidInput, match="L4"):
        svc.start(DEVICE, "QUICK", RENTAL, CHECKPOINT, profile="FULL")
    svc.start(DEVICE, "QUICK", RENTAL, CHECKPOINT)  # 빠른 검사는 L4가 없어도 된다


def test_옆면_카메라가_있으면_전체_검사의_옆면만_그_카메라로_CAM1_이름으로_찍는다(tmp_path, cams, presets):
    side = FakeCamera(Camera.CAM1)
    service = CaptureService(Store(tmp_path / "b.db"), cams, presets, tmp_path / "d", min_free_bytes=0,
                             inspection_lists={"full": full_inspection_shotlist(side_camera=True)},
                             list_cameras={"side": {Camera.CAM1: side}})
    done = _shoot_full(service, skip_scr1=None)
    photos = _photos(service, done.id)
    assert side.count == 11  # 옆면 5면 × L1·L2 + SCR1
    assert cams[Camera.CAM1].count == 9 and cams[Camera.CAM2].count == 9  # BOT·TOP·KB
    assert all(p.camera == "CAM1" for p in photos if p.face in {f.value for f in SIDE_FACES})


def test_전체_검사는_판정_조명을_골라도_세트_사진_전부를_탐지한다(service):
    done = _shoot_full(service)
    inspector = _inspector(service, defect_on="_KB_L4_CAM2_")
    inspector.lights = frozenset({"L3"})  # 빠른 검사의 판정 시간 줄이기 옵션
    inspector.start(done.id)

    r = result_json(service.store, done.id, shooting=False)
    assert r["status"] == "DONE" and r["profile"] == "FULL" and r["verdict"] == "DEFECT"
    assert len(r["photos"]) == 28 and r["faces"]["KB"] == 1


def test_빠른_검사는_판정_조명_옵션을_그대로_따른다(service):
    s = service.start(DEVICE, "QUICK", RENTAL, CHECKPOINT)
    run_to_end(service, s.id)
    done = service.complete(s.id)
    inspector = _inspector(service)
    inspector.lights = frozenset({"L3"})
    inspector.start(done.id)
    r = result_json(service.store, done.id, shooting=False)
    assert r["profile"] == "QUICK" and len(r["photos"]) == 4


@pytest.fixture
def server() -> FakeCapture:
    return FakeCapture()


@pytest.fixture
def upload(service, server):
    client = CaptureClient(BASE, BoothAuth(BASE, "booth-1", SECRET, http=server), http=server)
    clock = (f"2026-11-08T10:00:{i:02d}+09:00" for i in itertools.count())
    return lambda sid: upload_quick(service.store, sid, client, lambda: next(clock), http=server,
                                    sleep=lambda _: None)


def test_전체_검사는_FULL_세트로_28장을_올리고_SCR1_사유와_전체_탐지를_보낸다(service, server, upload):
    done = _shoot_full(service)
    _inspector(service, defect_on="_SL_L2_CAM1_").start(done.id)

    upload(done.id)

    (set_id, s), = server.sets.items()
    assert s["request"]["profile"] == "FULL" and s["status"] == "UPLOADED" and len(server.objects) == 28
    assert s["skipped"] == {"SCR1": SCR1_REASON}
    assert len(s["analyzed"]) == 28
    (d,) = server.detections[set_id]
    assert (d["face"], d["light"], d["camera"]) == ("SL", "L2", "CAM1")
    assert upload_json(service.store, done.id)["detections"] == "SENT"
    assert service.store.quick_sessions_to_upload() == []


def test_빠른_검사는_여전히_QUICK_세트로_올린다(service, server, upload):
    s = service.start(DEVICE, "QUICK", RENTAL, CHECKPOINT)
    run_to_end(service, s.id)
    done = service.complete(s.id)
    _inspector(service).start(done.id)
    upload(done.id)
    (s,) = server.sets.values()
    assert s["request"]["profile"] == "QUICK" and len(server.objects) == 12


@pytest.fixture
def web(service):
    server = make_server(App(service, quick=_inspector(service)), "127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def test_화면은_검사_범위_두_가지와_기본_빠른_검사를_받고_고른_범위로_시작한다(web):
    status, _, body = call(web, "GET", "/api/inspection-profiles")
    data = json.loads(body)
    assert status == 200 and data["default"] == "QUICK"
    assert [(p["name"], p["photos"]) for p in data["profiles"]] == [("QUICK", 12), ("FULL", 28)]

    start = {"mode": "QUICK", "deviceId": DEVICE, "rentalId": RENTAL, "checkpointId": CHECKPOINT}
    status, _, body = call(web, "POST", "/api/sessions", {**start, "profile": "FULL"})
    state = json.loads(body)
    assert status == 201 and state["profile"] == "FULL" and state["step"]["face"] == "BOT"
    assert call(web, "POST", "/api/sessions", {**start, "profile": "HALF"})[0] == 400


def test_전체_검사의_안내_단계는_다음에_찍을_면을_알려_화면이_진행_칸을_고른다(web):
    start = {"mode": "QUICK", "profile": "FULL", "deviceId": DEVICE, "rentalId": RENTAL, "checkpointId": CHECKPOINT}
    state = json.loads(call(web, "POST", "/api/sessions", start)[2])
    while state["step"]["kind"] != "NOTICE" or state["step"]["nextFace"] != "KB":
        if state["step"]["kind"] == "SHOT":
            call(web, "POST", "/api/sessions/current/shoot")
        state = json.loads(call(web, "POST", "/api/sessions/current/next", {"step": state["step"]["index"]})[2])
    assert "펼쳐" in state["step"]["instruction"]


def test_TRACE_BOOTH_INSPECTION은_quick_full만_받고_비우면_빠른_검사다():
    assert _inspection_profile("") == "QUICK"
    assert _inspection_profile("full") == "FULL"
    assert _inspection_profile(" Quick ") == "QUICK"
    with pytest.raises(SystemExit):
        _inspection_profile("half")


def _hand_switches(steps) -> int:
    """조명을 손으로 바꾸는 횟수(앞 사진과 조명이 다른 사진 단계 수)."""
    lights = [s.light for s in steps if s.kind is StepKind.SHOT]
    return sum(1 for a, b in itertools.pairwise(lights) if a is not b)


def test_전체_검사_옆면은_면마다_L1_L2_순서를_뒤집어_조명을_덜_바꾼다():
    for side_camera in (False, True):
        steps = full_inspection_shotlist(side_camera)
        side = [(s.face, s.light) for s in steps if s.kind is StepKind.SHOT and s.face in SIDE_FACES]
        assert side[:10] == [
            (Face.SF, Light.L1), (Face.SF, Light.L2), (Face.SB, Light.L2), (Face.SB, Light.L1),
            (Face.SL, Light.L1), (Face.SL, Light.L2), (Face.SR, Light.L2), (Face.SR, Light.L1),
            (Face.SCR0, Light.L1), (Face.SCR0, Light.L2),
        ]
        assert side[10] == (Face.SCR1, Light.OFF)
        assert required_photo_count(steps) == 28
        assert _hand_switches(steps) == 13  # 예전 순서(면마다 L1→L2)는 17번


def test_앞_사진과_조명이_같으면_스위치를_만지지_말라고_안내한다():
    steps = FULL_INSPECTION_SHOTLIST
    shots = [s for s in steps if s.kind is StepKind.SHOT]
    for prev, cur in itertools.pairwise(shots):
        same = "그대로(앞 사진과 같음)" in cur.instruction
        assert same is (prev.light is cur.light)
        if not same:
            assert "켜세요" in cur.instruction or "끄세요" in cur.instruction
    quick = [s for s in QUICK_SHOTLIST if s.kind is StepKind.SHOT]
    assert [("그대로(앞 사진과 같음)" in s.instruction) for s in quick] == [False, False, False, True, False, False]


def test_같은_조명_안내도_어느_조명이_켜져_있어야_하는지_적는다():
    shots = [s for s in FULL_INSPECTION_SHOTLIST if s.kind is StepKind.SHOT]
    same = [s for s in shots if "그대로(앞 사진과 같음)" in s.instruction]
    assert same and all(f"{s.light.value}만 켜져 있는지 확인" in s.instruction for s in same)


def test_서버를_다시_켜_옆면_순서가_바뀌어도_다른_조명_사진을_찍은_것으로_치지_않는다(service):
    from dataclasses import replace

    s = service.start(DEVICE, "QUICK", RENTAL, CHECKPOINT, profile="FULL")
    run_to_end(service, s.id, skip_scr1=SCR1_REASON)
    assert service.missing(s.id) == []
    # 예전 순서로 찍었다고 치고 SB 두 단계의 조명을 바꾼 목록으로 다시 켠 경우
    steps = list(service.inspection_lists["full"])
    sb = [i for i, st in enumerate(steps) if st.face is Face.SB]
    a, b = sb
    steps[a], steps[b] = replace(steps[a], light=steps[b].light), replace(steps[b], light=steps[a].light)
    service.inspection_lists["full"] = tuple(steps)
    assert sorted(service.missing(s.id)) == ["SB_L1_CAM1", "SB_L2_CAM1"]


def test_서버를_다시_켜_순서가_바뀐_단계는_다시_찍기를_막고_그_단계를_안_찍은_것으로_본다(service):
    from dataclasses import replace

    from trace_booth.session import CaptureError

    s = service.start(DEVICE, "QUICK", RENTAL, CHECKPOINT, profile="FULL")
    steps = list(service.inspection_lists["full"])
    first_sb = next(i for i, st in enumerate(steps) if st.face is Face.SB)
    while service.view(s.id).step.index < first_sb:
        st = service.view(s.id).step
        if st.captures:
            service.shoot(s.id)
        service.advance(s.id)
    service.shoot(s.id)  # 지금 목록의 첫 SB 단계(L2)
    a, b = first_sb, first_sb + 1
    steps[a], steps[b] = replace(steps[a], light=steps[b].light), replace(steps[b], light=steps[a].light)
    service.inspection_lists["full"] = tuple(steps)  # 예전 순서로 다시 켠 경우
    before = len(service.store.shots(s.id))
    with pytest.raises(CaptureError, match="처음부터"):
        service.retake(s.id)  # 바뀐 번호의 조명으로 다시 찍지 않는다
    assert len(service.store.shots(s.id)) == before
    assert service.store.get_session(s.id).status == "ABANDONED"
    assert service.missing(s.id)  # 완료되지 않는다
    again = service.start(DEVICE, "QUICK", RENTAL, CHECKPOINT, profile="FULL")
    assert again.id != s.id and again.current_step == 0  # 같은 기기를 다시 고르면 새로 시작한다


def test_순서가_바뀐_세션은_기기를_다시_고르거나_완료를_누르면_닫히고_새로_시작한다(service):
    from dataclasses import replace

    from trace_booth.session import CaptureError

    s = service.start(DEVICE, "QUICK", RENTAL, CHECKPOINT, profile="FULL")
    run_to_end(service, s.id, skip_scr1=SCR1_REASON)
    steps = list(service.inspection_lists["full"])
    a, b = [i for i, st in enumerate(steps) if st.face is Face.SB]
    steps[a], steps[b] = replace(steps[a], light=steps[b].light), replace(steps[b], light=steps[a].light)
    service.inspection_lists["full"] = tuple(steps)
    with pytest.raises(CaptureError, match="처음부터"):
        service.complete(s.id)  # 목록 끝에서 완료를 눌러도 닫고 안내한다
    assert service.store.get_session(s.id).status == "ABANDONED"

    t = service.start(DEVICE, "QUICK", RENTAL, CHECKPOINT, profile="FULL")
    service.shoot(t.id)
    service.inspection_lists["full"] = tuple(FULL_INSPECTION_SHOTLIST)  # 같은 번호의 조명은 같아 그대로 이어 간다
    assert service.start(DEVICE, "QUICK", RENTAL, CHECKPOINT, profile="FULL").id == t.id
    service.inspection_lists["full"] = tuple(steps)  # 예전 순서로 끝까지 찍어 둔 세션
    run_to_end(service, t.id, skip_scr1=SCR1_REASON)
    service.inspection_lists["full"] = tuple(FULL_INSPECTION_SHOTLIST)  # 새 순서로 다시 켬
    u = service.start(DEVICE, "QUICK", RENTAL, CHECKPOINT, profile="FULL")
    assert u.id != t.id and service.store.get_session(t.id).status == "ABANDONED"


def test_서버를_다시_켜_순서가_바뀐_단계는_이어_찍기를_막는다(service):
    from dataclasses import replace

    from trace_booth.session import CaptureError

    s = service.start(DEVICE, "QUICK", RENTAL, CHECKPOINT, profile="FULL")
    steps = list(service.inspection_lists["full"])
    first_sb = next(i for i, st in enumerate(steps) if st.face is Face.SB)
    while service.view(s.id).step.index < first_sb:
        if service.view(s.id).step.captures:
            service.shoot(s.id)
        service.advance(s.id)
    service.shoot(s.id)
    steps[first_sb], steps[first_sb + 1] = (
        replace(steps[first_sb], light=steps[first_sb + 1].light),
        replace(steps[first_sb + 1], light=steps[first_sb].light),
    )
    service.inspection_lists["full"] = tuple(steps)
    before = len(service.store.shots(s.id))
    with pytest.raises(CaptureError, match="처음부터"):
        service.shoot(s.id)  # 안내와 실제 조명이 어긋난 채 이어 찍지 않는다
    assert len(service.store.shots(s.id)) == before
    assert service.store.get_session(s.id).status == "ABANDONED"


def test_옆면_안내_단계로_넘어가면_옆면_카메라를_미리_연다(
    tmp_path: Path, cams: dict[Camera, FakeCamera], presets: Presets
) -> None:
    side = FakeCamera(Camera.CAM1)
    asked = threading.Event()
    side.before_use = asked.set  # CameraGroups가 다는 훅 흉내
    service = CaptureService(Store(tmp_path / "b.db"), cams, presets, tmp_path / "d", min_free_bytes=0,
                             inspection_lists={"full": full_inspection_shotlist(side_camera=True)},
                             list_cameras={"side": {Camera.CAM1: side}})
    s = service.start(DEVICE, "QUICK", RENTAL, CHECKPOINT, profile="FULL")
    while True:
        view = service.view(s.id)
        if not view.step.captures and "옆면 카메라" in view.step.instruction:
            break
        assert not asked.is_set()  # 위·아래·키보드를 찍는 동안은 옆면을 열지 않는다
        if view.step.captures:
            service.shoot(s.id)
        service.advance(s.id)
    assert asked.wait(2)  # 노트북을 옆면 카메라 앞에 세우는 동안 미리 연다
