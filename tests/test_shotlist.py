"""이 파일이 확인하는 것: 전체(full) 촬영 목록. 학습 데이터용으로 기기 하나를 면·조명·카메라별로 빠짐없이 찍는 순서.
면: TOP 윗면, BOT 바닥, KB 키보드, SCR0 꺼진 화면, SCR1 켜진 흰 화면(선택), SF·SB·SL·SR 앞·뒤·왼쪽·오른쪽 옆면.
조명: L1 램프, L2 왼쪽 막대, L3 뒤쪽 막대, L4 키보드용(스침광 = 비스듬히 비춰 흠집을 드러내는 빛).
"""

from trace_booth.codes import Camera, Face, Light
from trace_booth.shotlist import DEFAULT_SHOTLIST, LIGHT_NAME, REQUIRED_FACES, StepKind, required_photo_count


def shot_steps():
    return [s for s in DEFAULT_SHOTLIST if s.kind is StepKind.SHOT]


# [상황] 필수 사진 수를 센다.
# [기대] 28장이다.
def test_필수_사진은_한_대에_28장이다():
    assert required_photo_count() == 28


# [상황] 필수 면 목록을 본다.
# [기대] 켜진 화면(SCR1)을 뺀 8면이다.
def test_필수_면은_SCR1을_뺀_8면이다():
    required = {s.face for s in shot_steps() if not s.optional}
    assert required == set(REQUIRED_FACES)
    assert Face.SCR1 not in required


# [상황] 촬영 목록의 첫 단계를 본다.
# [기대] 기기 ID 판(SLATE) 사진이고 CAM1로만 찍는다(사진 묶음이 어떤 기기인지 남기기 위함).
def test_첫_단계는_SLATE이고_CAM1만_찍는다():
    first = DEFAULT_SHOTLIST[0]
    assert first.kind is StepKind.SLATE
    assert first.cameras == (Camera.CAM1,)


# [상황] 1차(위에서 찍는 면) 단계를 본다.
# [기대] CAM1·CAM2 두 대로 찍고, 키보드는 L1·L2·L4, 윗면은 L1·L2·L3로 찍는다.
def test_패스_1은_위_카메라_두_대로_찍는다():
    for s in shot_steps():
        if s.face in (Face.TOP, Face.BOT, Face.KB):
            assert s.cameras == (Camera.CAM1, Camera.CAM2)
    assert [s.light for s in shot_steps() if s.face is Face.KB] == [Light.L1, Light.L2, Light.L4]
    assert [s.light for s in shot_steps() if s.face is Face.TOP] == [Light.L1, Light.L2, Light.L3]


# [상황] 2차(옆면 등) 단계를 본다.
# [기대] 면마다 L1·L2로만, CAM1으로만 찍는다.
def test_패스_2는_면마다_L1과_L2를_CAM1로만_찍고_L3는_켜지_않는다():
    for face in (Face.SF, Face.SB, Face.SL, Face.SR, Face.SCR0):
        steps = [s for s in shot_steps() if s.face is face]
        assert [s.light for s in steps] == [Light.L1, Light.L2]
        assert all(s.cameras == (Camera.CAM1,) for s in steps)


# [상황] 켜진 화면(SCR1) 단계를 본다.
# [기대] 조명을 끄고 CAM1로 한 장 찍는 선택 단계다.
def test_SCR1은_조명_OFF로_한_장_찍는_선택_단계다():
    (scr1,) = [s for s in shot_steps() if s.face is Face.SCR1]
    assert scr1.optional and scr1.light is Light.OFF and scr1.cameras == (Camera.CAM1,)


# [상황] 스침광 단계의 안내 문구를 본다.
# [기대] "그 조명만 켜고 큰 조명 포함 나머지는 끄기"라고 안내한다(빛이 섞이면 흠집이 안 보임).
def test_스침광_단계는_그_조명만_켜라고_안내한다():
    for s in shot_steps():
        if s.light in (Light.L2, Light.L3, Light.L4):
            assert f"{LIGHT_NAME[s.light]}만 켜세요" in s.instruction and "큰 조명 포함 나머지는 끄기" in s.instruction


# [상황] 키보드 촬영 바로 앞 단계를 본다.
# [기대] 막대 조명 2를 앞쪽 자리로 옮기라는 안내 단계가 있다.
def test_KB_전에_바_2를_옮기라고_안내한다():
    kb = next(s.index for s in shot_steps() if s.face is Face.KB)
    assert DEFAULT_SHOTLIST[kb - 1].kind is StepKind.NOTICE
    assert "앞쪽 자리로 옮기" in DEFAULT_SHOTLIST[kb - 1].instruction


# [상황] 단계마다 붙은 번호를 본다.
# [기대] 0부터 차례대로 목록 순서와 같다.
def test_단계_번호는_목록_순서와_같다():
    assert [s.index for s in DEFAULT_SHOTLIST] == list(range(len(DEFAULT_SHOTLIST)))
