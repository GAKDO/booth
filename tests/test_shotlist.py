from trace_booth.codes import Camera, Face, Light
from trace_booth.shotlist import DEFAULT_SHOTLIST, LIGHT_NAME, REQUIRED_FACES, StepKind, required_photo_count


def shot_steps():
    return [s for s in DEFAULT_SHOTLIST if s.kind is StepKind.SHOT]


def test_필수_사진은_한_대에_28장이다():
    assert required_photo_count() == 28


def test_필수_면은_SCR1을_뺀_8면이다():
    required = {s.face for s in shot_steps() if not s.optional}
    assert required == set(REQUIRED_FACES)
    assert Face.SCR1 not in required


def test_첫_단계는_SLATE이고_CAM1만_찍는다():
    first = DEFAULT_SHOTLIST[0]
    assert first.kind is StepKind.SLATE
    assert first.cameras == (Camera.CAM1,)


def test_패스_1은_위_카메라_두_대로_찍는다():
    for s in shot_steps():
        if s.face in (Face.TOP, Face.BOT, Face.KB):
            assert s.cameras == (Camera.CAM1, Camera.CAM2)
    assert [s.light for s in shot_steps() if s.face is Face.KB] == [Light.L1, Light.L2, Light.L4]
    assert [s.light for s in shot_steps() if s.face is Face.TOP] == [Light.L1, Light.L2, Light.L3]


def test_패스_2는_면마다_L1과_L2를_CAM1로만_찍고_L3는_켜지_않는다():
    for face in (Face.SF, Face.SB, Face.SL, Face.SR, Face.SCR0):
        steps = [s for s in shot_steps() if s.face is face]
        assert [s.light for s in steps] == [Light.L1, Light.L2]
        assert all(s.cameras == (Camera.CAM1,) for s in steps)


def test_SCR1은_조명_OFF로_한_장_찍는_선택_단계다():
    (scr1,) = [s for s in shot_steps() if s.face is Face.SCR1]
    assert scr1.optional and scr1.light is Light.OFF and scr1.cameras == (Camera.CAM1,)


def test_스침광_단계는_그_조명만_켜라고_안내한다():
    for s in shot_steps():
        if s.light in (Light.L2, Light.L3, Light.L4):
            assert f"{LIGHT_NAME[s.light]}만 켜세요" in s.instruction and "큰 조명 포함 나머지는 끄기" in s.instruction


def test_KB_전에_바_2를_옮기라고_안내한다():
    kb = next(s.index for s in shot_steps() if s.face is Face.KB)
    assert DEFAULT_SHOTLIST[kb - 1].kind is StepKind.NOTICE
    assert "앞쪽 자리로 옮기" in DEFAULT_SHOTLIST[kb - 1].instruction


def test_단계_번호는_목록_순서와_같다():
    assert [s.index for s in DEFAULT_SHOTLIST] == list(range(len(DEFAULT_SHOTLIST)))
