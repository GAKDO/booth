"""조명별 카메라 고정값(노출·화이트밸런스·초점). 값은 부스 설정 날 실측해 JSON으로 받는다."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .codes import Light


class PresetError(Exception):
    pass


@dataclass(frozen=True)
class Presets:
    by_light: dict[str, dict[str, int]] = field(default_factory=dict)
    source: str | None = None

    @classmethod
    def load(cls, path: Path) -> Presets:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise PresetError("프리셋 파일은 {조명: {컨트롤: 값}} 형식이어야 함")
        for light, controls in data.items():
            Light(light)
            if not isinstance(controls, dict) or not all(isinstance(v, int) for v in controls.values()):
                raise PresetError(f"{light}의 값은 정수 컨트롤이어야 함")
        return cls(data, str(path))

    @property
    def configured(self) -> bool:
        return self.source is not None

    def for_light(self, light: Light) -> dict[str, int]:
        if not self.configured:
            return {}
        if light.value not in self.by_light:
            raise PresetError(f"프리셋 파일에 {light.value} 설정이 없음")
        return dict(self.by_light[light.value])
