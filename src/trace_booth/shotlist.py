"""촬영 목록.

- mvp(기본, 11/3 방향 전환): BOT → 뒤집어 TOP, 면마다 L1·L2·L3, CAM1·CAM2 = 12장
- quick(빠른 검사 모드): mvp에서 ID 카드만 뺀 것
- full(보관): GAKDO/docs booth/README.md 순서와 11/2 결정(패스 2 L1·L2, SCR1 선택), 28장
- 전체 검사(FULL, 11/8): 검사 모드에서 고르는 28장 목록. ID 카드 없이 BOT → TOP → 펼친 KB → 옆면(SCR1 선택)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .codes import FACE_REQUIRED_BOOTH, Camera, Face, Light


class StepKind(str, Enum):
    SLATE = "SLATE"
    SHOT = "SHOT"
    NOTICE = "NOTICE"


@dataclass(frozen=True)
class Step:
    index: int
    kind: StepKind
    instruction: str
    face: Face | None = None
    light: Light | None = None
    cameras: tuple[Camera, ...] = ()
    optional: bool = False

    @property
    def captures(self) -> bool:
        return self.kind in (StepKind.SLATE, StepKind.SHOT)


REQUIRED_FACES = FACE_REQUIRED_BOOTH

# 새 부스(11/10 C 도면 v19)는 앞문을 닫아야 11/5 밝기가 나온다(밝기를 문 닫고 맞춤). 자세를 바꾸면 문을 열므로 면 자세 안내마다 붙인다
DOOR = "앞문을 닫고 찍으세요"

_POSE = {
    Face.TOP: f"TOP: 닫아서 상판이 위, {DOOR}",
    Face.BOT: f"BOT: 앞 모서리를 들어 뒤로 넘겨 바닥이 위(좌우로 굴리지 않기), {DOOR}",
    Face.KB: f"KB: 펼쳐서 100° 가이드에 맞춤, {DOOR}",
    Face.SF: f"SF(앞 모서리)가 옆면 카메라 쪽을 향하게, {DOOR}",
    Face.SB: f"SB(뒤 힌지 쪽)가 옆면 카메라 쪽을 향하게, {DOOR}",
    Face.SL: f"SL(왼쪽 면)이 옆면 카메라 쪽을 향하게, {DOOR}",
    Face.SR: f"SR(오른쪽 면)이 옆면 카메라 쪽을 향하게, {DOOR}",
    Face.SCR0: f"SCR0: 화면을 끈 채 펼쳐 화면 쪽이 옆면 카메라 쪽을 향하게, {DOOR}",
    Face.SCR1: f"SCR1: 화면을 켜고 전체를 흰색으로 띄움(못 띄우면 건너뛰기), {DOOR}",
}
_TOP_CAMS = (Camera.CAM1, Camera.CAM2)
_SIDE_CAMS = (Camera.CAM1,)


# 11/4 조명 장비(민서 11/3 주문): L1 = VIJIM VL49(확산), L2·L3 = 5V USB LED 바 35cm 6000K 두 개(스침광).
# 11/5 실기 확정 배치: L1 큰 확산지·2단계, L2 왼쪽 위에 붙이고 A4로 가림, L3 뒤쪽 바닥(capture/README.md 3절).
# 11/10 최종 부스(C 도면 v19): 조명 자리는 11/5 그대로, 종이 확산지를 평판 틀로 바꾸고 암막 틀·오븐식 앞문을 덮었다.
# L2·L3 버튼은 책상 위 스위치 판에 모였고, VL49는 배터리가 들어 있어 오른쪽 옆 천을 들치고 램프 스위치를 손으로 민다.
LIGHT_GEAR = {
    Light.L1: "VL49(빔 오른쪽 끝, 확산판 틀 뒤, 2단계) · 오른쪽 옆 천을 들치고 램프 스위치",
    Light.L2: "LED 바 #1(빔 왼쪽 끝 아래) · 스위치 판 L2 버튼",
    Light.L3: "LED 바 #2(바닥 스토퍼 자리) · 스위치 판 L3 버튼",
    Light.L4: "LED 바 #2(앞 L4 자리로 옮김) · 스위치 판 L3 버튼",
}
def light_instruction(light: Light) -> str:
    if light is Light.OFF:
        return "조명을 모두 끄세요"
    if light is Light.L1:
        text = "L1만 켜세요(나머지는 끄기)"
    else:
        text = f"{light.value}만 켜세요(L1 포함 나머지는 끄기)"
    return f"{text} · {LIGHT_GEAR[light]}"


def light_text(light: Light, previous: Light | None) -> str:
    """앞 사진과 같은 조명이면 '그대로'라고만 말한다(11/11). 조명을 다시 만지다 끄거나 바꾸는 실수를 줄인다."""
    if previous is light:
        return f"조명 그대로({light.value}), 스위치를 만지지 마세요"
    return light_instruction(light)


def _previous_light(raw: list[tuple]) -> Light | None:
    """raw 목록에서 바로 앞 사진 단계의 조명(공지 단계는 건너뛰고, ID 카드 단계면 None으로 전체 안내를 보인다)."""
    for item in reversed(raw):
        if item[0] is StepKind.SHOT:
            return item[3]
        if item[0] is StepKind.SLATE:
            return None
    return None


def _build() -> tuple[Step, ...]:
    raw: list[tuple] = [(StepKind.SLATE, "L1을 켜고 ID 카드를 받침에 세우세요", None, None, _SIDE_CAMS, False)]

    def shots(face: Face, lights: tuple[Light, ...], cams: tuple[Camera, ...], optional: bool = False) -> None:
        for light in lights:
            raw.append((StepKind.SHOT, f"{_POSE[face]} · {light_instruction(light)}", face, light, cams, optional))

    def notice(text: str) -> None:
        raw.append((StepKind.NOTICE, text, None, None, (), False))

    shots(Face.TOP, (Light.L1, Light.L2, Light.L3), _TOP_CAMS)
    shots(Face.BOT, (Light.L1, Light.L2, Light.L3), _TOP_CAMS)
    notice("바 #2(뒤쪽 L3)를 앞 자리(L4)로 옮기세요")
    shots(Face.KB, (Light.L1, Light.L2, Light.L4), _TOP_CAMS)
    notice("CAM1을 삼각대에서 풀어 책상에 세우고, 배경판을 노트북 뒤 30cm에 두세요")
    for face in (Face.SF, Face.SB, Face.SL, Face.SR, Face.SCR0):
        shots(face, (Light.L1, Light.L2), _SIDE_CAMS)
    shots(Face.SCR1, (Light.OFF,), _SIDE_CAMS, optional=Face.SCR1 not in REQUIRED_FACES)
    notice("CAM1을 다시 끼우고 볼헤드 감시선을 맞추세요")
    return tuple(Step(i, k, text, f, li, cams, opt) for i, (k, text, f, li, cams, opt) in enumerate(raw))


MVP_LIGHTS = (Light.L1, Light.L2, Light.L3)
SIDE_LIGHTS = (Light.L1, Light.L2)


def _build_mvp(lights: tuple[Light, ...] = MVP_LIGHTS, slate: bool = True) -> tuple[Step, ...]:
    raw: list[tuple] = [(StepKind.SLATE, "L1을 켜고 ID 카드를 받침에 세우세요", None, None, _SIDE_CAMS)] if slate else []
    for face in (Face.BOT, Face.TOP):
        order = lights
        if face is Face.TOP:
            raw.append((StepKind.NOTICE, f"노트북을 다시 뒤집어 상판이 위로 오게 놓고, {DOOR}", None, None, ()))
            # TOP은 거꾸로(L3 → L2 → L1): 뒤집는 동안 켜 둔 바를 그대로 쓰고, 손으로 켜는 VL49(L1)는 한 대에 한 번만 다시 켠다
            order = tuple(reversed(lights))
        for light in order:
            text = light_text(light, _previous_light(raw))
            raw.append((StepKind.SHOT, f"{_POSE[face]} · {text}", face, light, _TOP_CAMS))
    return tuple(Step(i, k, text, f, li, cams) for i, (k, text, f, li, cams) in enumerate(raw))


def _build_kb() -> tuple[Step, ...]:
    """키보드만(11/5): 카메라는 위에 그대로 두고 펼친 노트북을 L1·L2·L4로 찍는다(CAM1·CAM2, 6장)."""
    raw: list[tuple] = [
        (StepKind.SLATE, "L1을 켜고 ID 카드를 받침에 세우세요", None, None, _SIDE_CAMS, False),
        (StepKind.NOTICE, "노트북을 펼쳐 100° 가이드에 맞추고, 바 #2(뒤쪽 L3)를 앞 자리(L4)로 옮기세요", None, None, (), False),
    ]
    for light in (Light.L1, Light.L2, Light.L4):
        raw.append((StepKind.SHOT, f"{_POSE[Face.KB]} · {light_instruction(light)}", Face.KB, light, _TOP_CAMS, False))
    return tuple(Step(i, k, text, f, li, cams, opt) for i, (k, text, f, li, cams, opt) in enumerate(raw))


def _build_side() -> tuple[Step, ...]:
    """옆면만(11/5): 책상 높이 카메라로 여러 대를 몰아서 찍는다(CAM1 이름, 10장 + SCR1 선택).

    TRACE_BOOTH_SIDE_CAM이 있으면 그 카메라(11/6 Brio)로, 없으면 CAM1을 책상에 내려 찍는다.
    """
    raw: list[tuple] = [(StepKind.SLATE, "L1을 켜고 ID 카드를 받침에 세우세요", None, None, _SIDE_CAMS, False)]
    for face in (Face.SF, Face.SB, Face.SL, Face.SR, Face.SCR0):
        for light in (Light.L1, Light.L2):
            raw.append((StepKind.SHOT, f"{_POSE[face]} · {light_instruction(light)}", face, light, _SIDE_CAMS, False))
    raw.append((StepKind.SHOT, f"{_POSE[Face.SCR1]} · {light_instruction(Light.OFF)}", Face.SCR1, Light.OFF, _SIDE_CAMS, Face.SCR1 not in REQUIRED_FACES))
    return tuple(Step(i, k, text, f, li, cams, opt) for i, (k, text, f, li, cams, opt) in enumerate(raw))


DEFAULT_SHOTLIST: tuple[Step, ...] = _build()  # full(보관)
MVP_SHOTLIST: tuple[Step, ...] = _build_mvp()
KB_SHOTLIST: tuple[Step, ...] = _build_kb()
SIDE_SHOTLIST: tuple[Step, ...] = _build_side()
# 빠른 검사: 기기는 태블릿 목록에서 골랐으므로 ID 카드(SLATE) 없이 BOT → 뒤집어 TOP, 12장
QUICK_SHOTLIST: tuple[Step, ...] = _build_mvp(slate=False)
# 11/5 학습 촬영은 mvp(위·아래) → kb(노트북마다) → side(마지막에 몰아서) 순서. 목록을 바꿀 땐 하던 촬영을 끝내고 서버를 다시 켠다.
SHOTLISTS: dict[str, tuple[Step, ...]] = {
    "mvp": MVP_SHOTLIST,
    "full": DEFAULT_SHOTLIST,
    "kb": KB_SHOTLIST,
    "side": SIDE_SHOTLIST,
}


# 전체 검사(FULL, 11/8 문서 감사 "부스는 QUICK 세트만 지원" 공백 메우기). 검사 모드(QUICK 흐름: 목록에서 기기 고르기 →
# 탐지 → 올리기)에서 세트 범위만 FULL로 바꾼다. 세션 기록의 shotlist 칸에 이 이름을 남기고, 비어 있으면 빠른 검사다.
FULL_INSPECTION = "full"
# codes.yaml capture.profile 값. 세트 생성의 profile로 그대로 보낸다
PROFILE_QUICK = "QUICK"
PROFILE_FULL = "FULL"
# 옆면 카메라(책상 높이)로 찍는 면. 위·아래·KB는 위 카메라 두 대
SIDE_FACES = (Face.SF, Face.SB, Face.SL, Face.SR, Face.SCR0, Face.SCR1)

# 전체 검사의 자세 안내. 옆면은 노트북을 닫은 채 세워 그 면이 카메라를 향하게 돌린다
_FULL_POSE = {
    Face.KB: f"KB: 노트북을 펼쳐 화면을 100° 가이드에 맞추고 키보드가 위 카메라를 보게, {DOOR}",
    Face.SF: f"SF: 닫은 채 앞 모서리(트랙패드 쪽)가 옆면 카메라를 향하게, {DOOR}",
    Face.SB: f"SB: 닫은 채 뒤 힌지 쪽이 옆면 카메라를 향하게, {DOOR}",
    Face.SL: f"SL: 닫은 채 왼쪽 면(포트)이 옆면 카메라를 향하게, {DOOR}",
    Face.SR: f"SR: 닫은 채 오른쪽 면(포트)이 옆면 카메라를 향하게, {DOOR}",
    Face.SCR0: f"SCR0: 펼쳐서 화면을 끈 채 화면 쪽이 옆면 카메라를 향하게, {DOOR}",
    Face.SCR1: f"SCR1: 화면을 켜고 전체를 흰색으로 띄움(못 띄우면 사유를 적고 건너뛰기), {DOOR}",
}


def full_inspection_shotlist(side_camera: bool = False) -> tuple[Step, ...]:
    """전체 검사(FULL) 28장 + SCR1 선택 1장.

    BOT·TOP은 빠른 검사와 같은 순서(BOT L1→L2→L3, 뒤집어 TOP L3→L2→L1)라 앞 12장은 빠른 검사와 같다. 이어서 펼친 KB를
    L1·L2·L4로(CAM1·CAM2), 옆면 5면을 L1·L2로, SCR1을 조명 없이 찍는다(옆면은 CAM1 코드 한 장씩, 계약 그대로).
    side_camera면 옆면을 책상 높이 옆면 카메라(TRACE_BOOTH_SIDE_CAM)로 찍고, 없으면 CAM1을 책상에 내려 찍는다.
    옆면 전에 바 #2를 뒤쪽(L3) 자리로 되돌린다. 앞(L4) 자리는 옆면 카메라와 노트북 사이라 면을 가리고,
    되돌려 두면 다음 빠른 검사도 같은 조명으로 찍힌다.
    """
    raw: list[tuple] = []

    def notice(text: str) -> None:
        raw.append((StepKind.NOTICE, text, None, None, (), False))

    def shots(face: Face, lights: tuple[Light, ...], cams: tuple[Camera, ...], optional: bool = False) -> None:
        pose = _FULL_POSE.get(face, _POSE[face])
        for light in lights:
            text = light_text(light, _previous_light(raw))
            raw.append((StepKind.SHOT, f"{pose} · {text}", face, light, cams, optional))

    shots(Face.BOT, MVP_LIGHTS, _TOP_CAMS)
    notice(f"노트북을 다시 뒤집어 상판이 위로 오게 놓고, {DOOR}")
    shots(Face.TOP, tuple(reversed(MVP_LIGHTS)), _TOP_CAMS)
    notice(f"노트북을 펼쳐 화면을 100° 가이드에 맞추고, 바 #2(뒤쪽 L3)를 앞 자리(L4)로 옮긴 뒤 {DOOR}")
    shots(Face.KB, (Light.L1, Light.L2, Light.L4), _TOP_CAMS)
    # 새 부스(11/10): L4 자리는 노트북 앞~문 사이라 옆면 카메라와 노트북 사이에 놓여 찍을 면을 가린다.
    # 옆면은 L1·L2만 쓰므로 바 #2를 옆면 전에 뒤쪽(L3) 자리로 되돌린다. 다음 빠른 검사 준비도 이걸로 끝난다.
    back = "바 #2를 뒤쪽(L3) 자리로 되돌리고"
    if side_camera:
        notice(f"노트북을 닫고, {back} 노트북을 옆면 카메라 앞 바닥 선에 세우세요. 옆면은 L1·L2만 써요. {DOOR}")
    else:
        notice(f"노트북을 닫고, {back} CAM1을 삼각대에서 풀어 책상에 세우고 배경판을 노트북 뒤 30cm에 두세요. {DOOR}")
    # 옆면은 면마다 L1·L2를 번갈아 순서를 뒤집는다(L1→L2, L2→L1, …, 11/11). 손으로 켜는 VL49(L1)와 L2 버튼을
    # 면마다 두 번씩(10번) 만지던 것이 면 사이에서는 그대로 두게 되어 6번으로 준다. 찍는 사진(면·조명)은 같다.
    for i, face in enumerate((Face.SF, Face.SB, Face.SL, Face.SR, Face.SCR0)):
        shots(face, SIDE_LIGHTS if i % 2 == 0 else tuple(reversed(SIDE_LIGHTS)), _SIDE_CAMS)
    shots(Face.SCR1, (Light.OFF,), _SIDE_CAMS, optional=Face.SCR1 not in REQUIRED_FACES)
    if not side_camera:
        notice("CAM1을 다시 끼워 볼헤드 감시선을 맞추세요(다음 빠른 검사 준비)")
    return tuple(Step(i, k, text, f, li, cams, opt) for i, (k, text, f, li, cams, opt) in enumerate(raw))


FULL_INSPECTION_SHOTLIST: tuple[Step, ...] = full_inspection_shotlist()


def inspection_profile(shotlist: str | None) -> str:
    """검사 세션의 shotlist 칸 → 세트 범위(codes.yaml capture.profile). 비어 있으면 빠른 검사."""
    return PROFILE_FULL if shotlist == FULL_INSPECTION else PROFILE_QUICK


def lights_used(steps: tuple[Step, ...]) -> set[Light]:
    """목록이 쓰는 조명(프리셋 파일에 있어야 하는 것)."""
    return {s.light for s in steps if s.captures and s.light is not None}


# 학습 화면에서 고를 때 보이는 이름(11/6)
SHOTLIST_LABELS: dict[str, str] = {
    "mvp": "위·아래(BOT·TOP)",
    "kb": "펼친 상태(키보드)",
    "side": "옆면(책상 높이 카메라)",
    "full": "전체(보관용)",
}


def required_photo_count(steps: tuple[Step, ...] = DEFAULT_SHOTLIST) -> int:
    return sum(len(s.cameras) for s in steps if s.kind is StepKind.SHOT and not s.optional)
