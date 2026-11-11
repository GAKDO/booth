"""버튼 → 판정 시간 재기(NFR-01). 목표 30초 안쪽, 기준 60초(11/5 민서: 시연에서 1분쯤이면 됨)."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

TARGET_SECONDS = 30.0
LIMIT_SECONDS = 60.0


def grade(seconds: float) -> str:
    """OK(목표 안), SLOW(목표 넘음, 기준 안), OVER(기준 넘음)."""
    if seconds <= TARGET_SECONDS:
        return "OK"
    return "SLOW" if seconds <= LIMIT_SECONDS else "OVER"


@dataclass
class Timeline:
    """빠른 검사 한 번의 구간 기록. 버튼을 누를 때 만들고, 단계가 끝날 때마다 mark("촬영 BOT") 처럼 부른다."""

    clock: Callable[[], float] = time.perf_counter
    start: float = field(init=False)
    marks: list[tuple[str, float]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.start = self.clock()

    def mark(self, name: str) -> float:
        """지금까지 걸린 시간(초)을 돌려주고 기록한다."""
        at = self.clock() - self.start
        self.marks.append((name, at))
        return at

    @property
    def total(self) -> float:
        return self.marks[-1][1] if self.marks else 0.0

    def to_json(self) -> dict[str, object]:
        """{"total": 12.3, "grade": "OK", "steps": [{"name": "촬영 BOT", "seconds": 4.1, "at": 4.1}, ...]}"""
        steps, prev = [], 0.0
        for name, at in self.marks:
            steps.append({"name": name, "seconds": round(at - prev, 3), "at": round(at, 3)})
            prev = at
        return {"total": round(self.total, 3), "grade": grade(self.total), "steps": steps}
