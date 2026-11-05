"""11/3 방향 전환: MVP 촬영 목록은 BOT(L1·L2·L3) → 뒤집어 TOP(L3·L2·L1), CAM1·CAM2."""

from conftest import run_to_end

from trace_booth.session import CaptureService
from trace_booth.shotlist import MVP_SHOTLIST, SHOTLISTS, StepKind, required_photo_count
from trace_booth.store import Store


def test_MVP_목록은_BOT을_L1_L2_L3로_TOP을_L3_L2_L1로_두_카메라에서_찍어_12장이다() -> None:
    shots = [s for s in MVP_SHOTLIST if s.kind is StepKind.SHOT]
    assert [(s.face.value, s.light.value) for s in shots] == [
        ("BOT", "L1"), ("BOT", "L2"), ("BOT", "L3"), ("TOP", "L3"), ("TOP", "L2"), ("TOP", "L1"),
    ]
    assert all([c.value for c in s.cameras] == ["CAM1", "CAM2"] for s in shots)
    assert required_photo_count(MVP_SHOTLIST) == 12
    assert MVP_SHOTLIST[0].kind is StepKind.SLATE
    assert [s.index for s in MVP_SHOTLIST] == list(range(len(MVP_SHOTLIST)))


def test_MVP_목록으로_끝까지_찍으면_빠진_사진_없이_완료된다(tmp_path, cams, presets) -> None:
    service = CaptureService(
        Store(tmp_path / "booth.db"), cams, presets, tmp_path / "data", steps=SHOTLISTS["mvp"], min_free_bytes=0
    )
    sid = service.start("MB01").id
    run_to_end(service, sid)
    assert service.missing(sid) == []
    assert service.complete(sid).status == "COMPLETE"
    names = [s.filename for s in service.store.shots(sid) if s.kind == "SHOT"]
    assert len(names) == 12
    assert names[:2] == ["MB01_BOT_L1_CAM1_01.jpg", "MB01_BOT_L1_CAM2_01.jpg"]
    assert names[-1] == "MB01_TOP_L1_CAM2_01.jpg"


def test_조명_안내에_장비_이름이_붙는다() -> None:
    shots = [s for s in MVP_SHOTLIST if s.kind is StepKind.SHOT]
    assert "VL49" in shots[0].instruction
    assert "LED 바 #1" in shots[1].instruction and "LED 바 #2" in shots[2].instruction


def test_키보드_목록은_펼친_KB를_L1_L2_L4로_두_카메라에서_찍어_6장이다(tmp_path, cams, presets) -> None:
    from trace_booth.shotlist import KB_SHOTLIST

    shots = [s for s in KB_SHOTLIST if s.kind is StepKind.SHOT]
    assert [(s.face.value, s.light.value) for s in shots] == [("KB", "L1"), ("KB", "L2"), ("KB", "L4")]
    assert required_photo_count(KB_SHOTLIST) == 6
    assert KB_SHOTLIST[0].kind is StepKind.SLATE and KB_SHOTLIST[1].kind is StepKind.NOTICE
    service = CaptureService(
        Store(tmp_path / "booth.db"), cams, presets, tmp_path / "data", steps=SHOTLISTS["kb"], min_free_bytes=0
    )
    sid = service.start("MB02").id
    run_to_end(service, sid)
    assert service.complete(sid).status == "COMPLETE"
    names = [s.filename for s in service.store.shots(sid) if s.kind == "SHOT"]
    assert names[0] == "MB02_KB_L1_CAM1_01.jpg" and len(names) == 6


def test_옆면_목록은_CAM1로_네_옆면과_꺼진_화면을_L1_L2로_찍고_흰_화면은_선택이다() -> None:
    from trace_booth.shotlist import SIDE_SHOTLIST

    shots = [s for s in SIDE_SHOTLIST if s.kind is StepKind.SHOT]
    assert [f"{s.face.value}_{s.light.value}" for s in shots[:-1]] == [
        f"{f}_{li}" for f in ("SF", "SB", "SL", "SR", "SCR0") for li in ("L1", "L2")
    ]
    assert all([c.value for c in s.cameras] == ["CAM1"] for s in shots)
    assert shots[-1].face.value == "SCR1" and shots[-1].light.value == "OFF"
    assert required_photo_count(SIDE_SHOTLIST) == 10 + (0 if shots[-1].optional else 1)
    assert set(SHOTLISTS) >= {"mvp", "full", "kb", "side"}
