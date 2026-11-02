"""기본 촬영 목록. GAKDO/docs booth/README.md 순서와 11/2 결정(패스 2 L1·L2, SCR1 선택)을 따른다."""

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
    Face.BOT: "BOT: 뒤집어 바닥이 위",
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


def light_instruction(light: Light) -> str:
    if light is Light.OFF:
        return "조명을 모두 끄세요"
    if light is Light.L1:
        return "L1만 켜세요(나머지는 끄기)"
    return f"{light.value}만 켜세요(L1 포함 나머지는 끄기)"


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


DEFAULT_SHOTLIST: tuple[Step, ...] = _build()


def required_photo_count(steps: tuple[Step, ...] = DEFAULT_SHOTLIST) -> int:
    return sum(len(s.cameras) for s in steps if s.kind is StepKind.SHOT and not s.optional)
