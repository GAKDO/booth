"""11/3 방향 전환: MVP 촬영 목록은 BOT → 뒤집어 TOP, L1만, CAM1·CAM2."""

from conftest import run_to_end

from trace_booth.session import CaptureService
from trace_booth.shotlist import MVP_SHOTLIST, SHOTLISTS, StepKind, required_photo_count
from trace_booth.store import Store


def test_MVP_목록은_BOT_다음_TOP을_L1로_두_카메라에서_찍어_4장이다() -> None:
    shots = [s for s in MVP_SHOTLIST if s.kind is StepKind.SHOT]
    assert [s.face.value for s in shots] == ["BOT", "TOP"]
    assert {s.light.value for s in shots} == {"L1"}
    assert all([c.value for c in s.cameras] == ["CAM1", "CAM2"] for s in shots)
    assert required_photo_count(MVP_SHOTLIST) == 4
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
    assert names == [
        "MB01_BOT_L1_CAM1_01.jpg", "MB01_BOT_L1_CAM2_01.jpg",
        "MB01_TOP_L1_CAM1_01.jpg", "MB01_TOP_L1_CAM2_01.jpg",
    ]
