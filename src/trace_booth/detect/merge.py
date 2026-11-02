"""타일 사이 중복 제거: 같은 클래스끼리 상자 IoU가 기준 이상이면 신뢰도 높은 것만 남긴다."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Found:
    defect_class: str
    polygon: list[list[float]]  # 원본 사진 px
    confidence: float

    @property
    def box(self) -> tuple[float, float, float, float]:
        xs = [p[0] for p in self.polygon]
        ys = [p[1] for p in self.polygon]
        return min(xs), min(ys), max(xs), max(ys)


def iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def dedupe(found: list[Found], threshold: float = 0.5) -> list[Found]:
    kept: list[Found] = []
    for f in sorted(found, key=lambda x: x.confidence, reverse=True):
        if all(k.defect_class != f.defect_class or iou(k.box, f.box) < threshold for k in kept):
            kept.append(f)
    return kept
