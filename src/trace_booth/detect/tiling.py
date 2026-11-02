"""고해상 사진을 겹치는 타일로 자르고, 타일 좌표를 원본 좌표로 되돌린다."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Tile:
    x: int  # 원본에서 타일 왼쪽 위
    y: int
    w: int
    h: int


def _starts(length: int, size: int, step: int) -> list[int]:
    if length <= size:
        return [0]
    starts = list(range(0, length - size, step))
    starts.append(length - size)  # 마지막 타일은 끝에 붙인다
    return sorted(set(starts))


def tiles(width: int, height: int, size: int = 640, overlap: float = 0.2) -> list[Tile]:
    """사진 전체를 빠짐없이 덮는 타일. 이웃 타일은 size × overlap 이상 겹친다."""
    if not 0 <= overlap < 1:
        raise ValueError("overlap은 0 이상 1 미만")
    step = max(1, int(size * (1 - overlap)))
    return [
        Tile(x, y, min(size, width), min(size, height))
        for y in _starts(height, size, step)
        for x in _starts(width, size, step)
    ]


def to_image(polygon: list[tuple[float, float]], tile: Tile, width: int, height: int) -> list[list[float]]:
    """타일 안 폴리곤을 원본 px로. 사진 밖으로 나간 점은 가장자리로 자른다."""
    return [
        [min(max(px + tile.x, 0.0), float(width)), min(max(py + tile.y, 0.0), float(height))]
        for px, py in polygon
    ]
