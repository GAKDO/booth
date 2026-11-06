"""촬영 목록.

- mvp(기본, 11/3 방향 전환): BOT → 뒤집어 TOP, 면마다 L1·L2·L3, CAM1·CAM2 = 12장
- quick(빠른 검사 모드): mvp에서 ID 카드만 뺀 것
- full(보관): GAKDO/docs booth/README.md 순서와 11/2 결정(패스 2 L1·L2, SCR1 선택), 28장
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

_POSE = {
    Face.TOP: "TOP: 닫아서 상판이 위",
    Face.BOT: "BOT: 앞 모서리를 들어 뒤로 넘겨 바닥이 위(좌우로 굴리지 않기)",
    Face.KB: "KB: 펼쳐서 100° 가이드에 맞춤",
    Face.SF: "SF 면이 CAM1 쪽을 향하게",
    Face.SB: "SB 면이 CAM1 쪽을 향하게",
    Face.SL: "SL 면이 CAM1 쪽을 향하게",
    Face.SR: "SR 면이 CAM1 쪽을 향하게",
    Face.SCR0: "SCR0: 화면을 끈 채 화면 쪽이 CAM1 쪽을 향하게",
    Face.SCR1: "SCR1: 화면을 켜고 전체를 흰색으로 띄움(못 띄우면 건너뛰기)",
}
_TOP_CAMS = (Camera.CAM1, Camera.CAM2)
_SIDE_CAMS = (Camera.CAM1,)


# 11/4 조명 장비(민서 11/3 주문): L1 = VIJIM VL49(확산, 앞 기둥 위), L2·L3 = 5V USB LED 바 35cm 6000K 두 개(스침광).
# 11/5 실기 확정 배치: L1 큰 확산지·2단계, L2 왼쪽 위에 붙이고 A4로 가림, L3 뒤쪽 바닥(capture/README.md 3절).
# VL49는 배터리가 들어 있어 USB 전원을 끊어도 꺼지지 않으므로 손으로 켜고 끈다.
LIGHT_GEAR = {
    Light.L1: "VL49(앞 기둥 위, 큰 확산지 뒤, 2단계)",
    Light.L2: "LED 바 #1(왼쪽 위, A4로 가림)",
    Light.L3: "LED 바 #2(뒤쪽 바닥)",
    Light.L4: "LED 바 #2(앞 자리로 옮김)",
}


def light_instruction(light: Light) -> str:
    if light is Light.OFF:
        return "조명을 모두 끄세요"
    if light is Light.L1:
        text = "L1만 켜세요(나머지는 끄기)"
    else:
        text = f"{light.value}만 켜세요(L1 포함 나머지는 끄기)"
    return f"{text} · {LIGHT_GEAR[light]}"


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


def _build_mvp(lights: tuple[Light, ...] = MVP_LIGHTS, slate: bool = True) -> tuple[Step, ...]:
    raw: list[tuple] = [(StepKind.SLATE, "L1을 켜고 ID 카드를 받침에 세우세요", None, None, _SIDE_CAMS)] if slate else []
    for face in (Face.BOT, Face.TOP):
        order = lights
        if face is Face.TOP:
            raw.append((StepKind.NOTICE, "노트북을 다시 뒤집어 상판이 위로 오게 놓으세요", None, None, ()))
            # TOP은 거꾸로(L3 → L2 → L1): 뒤집는 동안 켜 둔 바를 그대로 쓰고, 손으로 켜는 VL49(L1)는 한 대에 한 번만 다시 켠다
            order = tuple(reversed(lights))
        for light in order:
            raw.append((StepKind.SHOT, f"{_POSE[face]} · {light_instruction(light)}", face, light, _TOP_CAMS))
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
    """옆면만(11/5): CAM1을 하루에 한 번만 책상에 내리고 여러 대를 몰아서 찍는다(CAM1, 10장 + SCR1 선택)."""
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


# 학습 화면에서 고를 때 보이는 이름(11/6)
SHOTLIST_LABELS: dict[str, str] = {
    "mvp": "위·아래(BOT·TOP)",
    "kb": "펼친 상태(키보드)",
    "side": "옆면(책상 높이 카메라)",
    "full": "전체(보관용)",
}


def required_photo_count(steps: tuple[Step, ...] = DEFAULT_SHOTLIST) -> int:
    return sum(len(s.cameras) for s in steps if s.kind is StepKind.SHOT and not s.optional)
