"""스토리 1: 학습 데이터 모드로 노트북 한 대 찍기 (수용 시나리오 문장을 테스트 이름으로)."""

import hashlib
import json
from pathlib import Path

import pytest
from conftest import run_to_end

from trace_booth.camera import CameraError
from trace_booth.codes import Camera, Face, Light
from trace_booth.naming import FILENAME_RE
from trace_booth.session import CaptureError, CaptureService, DiskFull, Incomplete, InvalidInput
from trace_booth.shotlist import StepKind, required_photo_count
from trace_booth.store import Store


def go_to(service: CaptureService, sid: str, face: Face, light: Light) -> None:
    while True:
        step = service.view(sid).step
        if step.face is face and step.light is light:
            return
        if step.captures:
            service.shoot(sid)
        service.advance(sid)


def test_기기_ID_MB01로_시작하면_첫_단계에_자세와_켤_조명과_남은_단계_수가_보인다(service):
    s = service.start("MB01")
    v = service.view(s.id)
    assert v.step.kind is StepKind.SLATE
    assert "L1을 켜고" in v.step.instruction
    assert v.remaining_steps == len(service.steps)


def test_TOP_L2_단계에서_버튼을_한_번_누르면_CAM1_CAM2_사진이_한_장씩_저장되고_이름이_규칙을_따른다(service):
    sid = service.start("MB01").id
    go_to(service, sid, Face.TOP, Light.L2)
    shots = service.shoot(sid)
    assert [s.filename for s in shots] == ["MB01_TOP_L2_CAM1_01.jpg", "MB01_TOP_L2_CAM2_01.jpg"]
    assert all(Path(s.local_path).is_file() for s in shots)
    assert service.view(sid).step.light is Light.L2 and service.view(sid).taken


def test_목록을_끝까지_찍으면_완료가_뜨고_빠진_면이_0개이며_장수가_목록과_같다(service):
    sid = service.start("MB01").id
    run_to_end(service, sid)
    assert service.missing(sid) == []
    done = service.complete(sid)
    assert done.status == "COMPLETE"
    shots = [s for s in service.store.shots(sid) if s.kind == "SHOT"]
    required = [s for s in shots if s.face != "SCR1"]
    assert len(required) == required_photo_count() == 28
    assert {s.face for s in required} == {"TOP", "BOT", "SF", "SB", "SL", "SR", "KB", "SCR0"}
    assert all(FILENAME_RE.match(s.filename) for s in shots)


def test_찍은_사진을_다시_읽어_계산한_SHA256이_기록과_같다(service):
    sid = service.start("MB01").id
    run_to_end(service, sid)
    for s in service.store.shots(sid):
        assert hashlib.sha256(Path(s.local_path).read_bytes()).hexdigest() == s.sha256


def test_패스_2_SL_단계는_L1_한_장_L2_한_장을_차례로_안내하고_CAM1만_찍는다(service, cams):
    sid = service.start("MB01").id
    go_to(service, sid, Face.SL, Light.L1)
    assert "L1만 켜세요" in service.view(sid).step.instruction
    cam2_before = cams[Camera.CAM2].count
    first = service.shoot(sid)
    service.advance(sid)
    assert "L2만 켜세요" in service.view(sid).step.instruction
    second = service.shoot(sid)
    assert [s.filename for s in first + second] == ["MB01_SL_L1_CAM1_01.jpg", "MB01_SL_L2_CAM1_01.jpg"]
    assert cams[Camera.CAM2].count == cam2_before


def test_SCR1_건너뛰기에_사유를_넣으면_SCR1_없이도_완료되고_사유가_남는다(service):
    sid = service.start("MB01").id
    run_to_end(service, sid, skip_scr1="로그인 없이 흰 화면을 못 띄움")
    done = service.complete(sid)
    assert done.status == "COMPLETE"
    assert done.scr1_skip_reason == "로그인 없이 흰 화면을 못 띄움"
    assert not any(s.face == "SCR1" for s in service.store.shots(sid))


def test_SCR1을_찍지도_건너뛰지도_않으면_완료되지_않는다(service):
    sid = service.start("MB01").id
    go_to(service, sid, Face.SCR1, Light.OFF)
    with pytest.raises(Incomplete) as e:
        service.complete(sid)
    assert e.value.missing == ["SCR1_OFF_CAM1"]


def test_사유_없이는_SCR1을_건너뛸_수_없고_필수_면은_건너뛸_수_없다(service):
    sid = service.start("MB01").id
    service.shoot(sid)  # SLATE
    with pytest.raises(CaptureError):
        service.skip_optional(sid, "이유")
    with pytest.raises(InvalidInput):
        service.skip_optional(sid, "  ")


def test_빠진_면이_있으면_완료되지_않고_빠진_사진을_알려_준다(service):
    sid = service.start("MB01").id
    service.shoot(sid)
    with pytest.raises(Incomplete) as e:
        service.complete(sid)
    assert "TOP_L1_CAM1" in e.value.missing and "SCR0_L2_CAM1" in e.value.missing


def test_카메라_한_대가_실패하면_단계가_넘어가지_않고_실패한_카메라를_알린다(service, cams):
    sid = service.start("MB01").id
    go_to(service, sid, Face.TOP, Light.L1)
    cams[Camera.CAM2].fail = True
    with pytest.raises(CameraError) as e:
        service.shoot(sid)
    assert e.value.camera is Camera.CAM2
    assert service.view(sid).step.face is Face.TOP and service.view(sid).step.light is Light.L1
    cams[Camera.CAM2].fail = False
    shots = service.shoot(sid)
    # 실패한 촬영은 CAM1도 남기지 않는다(짝 없는 사진 방지, 11/9) → 둘 다 01
    assert [s.filename for s in shots] == ["MB01_TOP_L1_CAM1_01.jpg", "MB01_TOP_L1_CAM2_01.jpg"]


def test_다시_찍으면_새_번호로_남고_이전_파일은_그대로다(service):
    sid = service.start("MB01").id
    go_to(service, sid, Face.TOP, Light.L1)
    first = service.shoot(sid)
    old = {s.local_path: Path(s.local_path).read_bytes() for s in first}
    again = service.retake(sid)
    assert [s.seq for s in again] == [2, 2]
    for path, data in old.items():
        assert Path(path).read_bytes() == data
    assert service.view(sid).step.light is Light.L1
    service.advance(sid)
    assert service.view(sid).step.light is Light.L2


def test_도구를_다시_켜면_그_기기의_마지막으로_끝난_단계_다음부터_이어_간다(tmp_path, cams, presets):
    db = tmp_path / "booth.db"
    a = CaptureService(Store(db), cams, presets, tmp_path / "data", min_free_bytes=0)
    sid = a.start("MB01").id
    a.shoot(sid)
    a.advance(sid)
    a.shoot(sid)
    a.advance(sid)
    b = CaptureService(Store(db), cams, presets, tmp_path / "data", min_free_bytes=0)
    resumed = b.start("MB01")
    assert resumed.id == sid
    assert b.view(sid).step.light is Light.L2


def test_잘못된_기기_ID로는_촬영을_시작하지_않는다(service):
    with pytest.raises(InvalidInput):
        service.start("mb1")


def test_디스크가_가득_차면_찍지_않는다(tmp_path, cams, presets):
    svc = CaptureService(Store(tmp_path / "b.db"), cams, presets, tmp_path, min_free_bytes=1 << 62)
    sid = svc.start("MB01").id
    with pytest.raises(DiskFull):
        svc.shoot(sid)
    assert svc.store.shots(sid) == []


def test_조명별_고정_설정을_적용하고_사진_기록에_남긴다(service, cams):
    sid = service.start("MB01").id
    go_to(service, sid, Face.TOP, Light.L2)
    shot = service.shoot(sid)[0]
    assert cams[Camera.CAM1].settings_seen[-1] == {"exposure_time_absolute": 101}
    assert json.loads(shot.camera_settings) == {"exposure_time_absolute": 101}


def test_안내_단계에서는_찍지_않고_다음으로만_넘어간다(service):
    sid = service.start("MB01").id
    go_to(service, sid, Face.BOT, Light.L3)
    service.shoot(sid)
    service.advance(sid)
    assert service.view(sid).step.kind is StepKind.NOTICE
    with pytest.raises(CaptureError):
        service.shoot(sid)
    service.advance(sid)
    assert service.view(sid).step.face is Face.KB


def test_촬영을_눌러도_단계는_그대로고_다음을_눌러야_넘어간다(service):
    sid = service.start("MB01").id
    go_to(service, sid, Face.TOP, Light.L1)
    assert not service.view(sid).taken
    service.shoot(sid)
    v = service.view(sid)
    assert v.step.face is Face.TOP and v.step.light is Light.L1 and v.taken
    service.advance(sid)
    assert service.view(sid).step.light is Light.L2 and not service.view(sid).taken


def test_촬영을_두_번_눌러도_같은_단계_이름으로_새_번호가_붙는다(service):
    sid = service.start("MB01").id
    go_to(service, sid, Face.TOP, Light.L1)
    first, second = service.shoot(sid), service.shoot(sid)
    assert [s.filename for s in first + second] == [
        "MB01_TOP_L1_CAM1_01.jpg", "MB01_TOP_L1_CAM2_01.jpg", "MB01_TOP_L1_CAM1_02.jpg", "MB01_TOP_L1_CAM2_02.jpg"]


def test_찍지_않은_단계는_다음으로_넘어가지_않는다(service, cams):
    sid = service.start("MB01").id
    go_to(service, sid, Face.TOP, Light.L1)
    with pytest.raises(CaptureError):
        service.advance(sid)
    cams[Camera.CAM2].fail = True
    with pytest.raises(CameraError):
        service.shoot(sid)  # CAM1만 찍힘
    with pytest.raises(CaptureError):
        service.advance(sid)
    assert service.view(sid).step.light is Light.L1


def test_다음을_두_번_보내도_안내_단계를_건너뛰지_않는다(service):
    sid = service.start("MB01").id
    go_to(service, sid, Face.BOT, Light.L3)
    service.shoot(sid)
    index = service.view(sid).step.index
    service.advance(sid, index)
    with pytest.raises(CaptureError):
        service.advance(sid, index)
    assert service.view(sid).step.kind is StepKind.NOTICE


def test_찍은_선택_단계는_건너뛰기로_넘길_수_없다(service):
    sid = service.start("MB01").id
    go_to(service, sid, Face.SCR1, Light.OFF)
    service.shoot(sid)
    with pytest.raises(CaptureError):
        service.skip_optional(sid, "흰 화면 불가")
    assert service.store.get_session(sid).scr1_skip_reason is None
