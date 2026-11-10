"""새 부스를 세운 뒤 조명 밝기·빛샘을 숫자로 확인한다(11/10, `trace-booth light-check`).

- 밝기: 11/5 학습 촬영 확정값(capture/README.md "11/5 확정값")과 같은 숫자로 조명 자리를 맞춘다.
  노트북 가운데 밝기 = 사진 가운데 40%×40% 칸의 회색 밝기 평균(0~255),
  날림 = 사진 전체에서 R·G·B 중 하나라도 255인 픽셀 비율(%).
  11/5에 쓴 계산과 똑같은지 알 수 없으므로, 같은 노트북의 11/5 사진을 `--images`로 먼저 재서 기준을 확인한다.
- 빛샘: 부스 조명을 모두 끄고 문을 닫은 사진의 가운데 밝기가 5 이하(C 도면 v19 "빛 새는지 확인").
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median

from .codes import Light

# 11/5 확정값(미드나이트 맥북, 노트북 가운데 밝기 하한·상한, 날림 % 상한)
LIGHT_TARGETS: dict[Light, tuple[float, float, float]] = {
    Light.L1: (34.0, 42.0, 0.01),
    Light.L2: (53.0, 71.0, 0.04),
    Light.L3: (98.0, 105.0, 0.01),
}
LEAK_MAX = 5.0  # 부스 조명을 다 끄고 문을 닫았을 때 가운데 밝기 상한
CENTER = 0.4  # 가운데 칸 한 변 = 사진 변의 40%


@dataclass(frozen=True)
class LightStats:
    center: float  # 가운데 칸 회색 밝기 평균 0~255
    clipped_pct: float  # 날린 픽셀 %


def measure_light(image) -> LightStats:  # image: BGR numpy 배열
    import numpy as np

    img = np.asarray(image)
    gray = img if img.ndim == 2 else img[..., :3] @ np.array([0.114, 0.587, 0.299])  # BGR → 회색(BT.601)
    h, w = gray.shape[:2]
    a, b = (1 - CENTER) / 2, (1 + CENTER) / 2
    center = float(gray[int(h * a):int(h * b), int(w * a):int(w * b)].mean())
    clipped = (img >= 255) if img.ndim == 2 else (img[..., :3] >= 255).any(axis=-1)
    return LightStats(center, float(clipped.mean() * 100))


def judge_light(light: Light | None, stats: LightStats, leak: bool = False,
                center_range: tuple[float, float] | None = None) -> tuple[bool, str]:
    """(통과, 사람이 읽을 한 줄). 기준이 없는 조명(L4 등)은 숫자만 보여 주고 통과로 둔다.

    center_range를 주면 가운데 밝기 기준만 그 값으로 바꾼다(11/5 사진을 이 계산으로 다시 잰 값이 README 숫자와 다를 때).
    """
    if leak:
        ok = stats.center <= LEAK_MAX
        return ok, f"빛샘 가운데 {stats.center:.1f} (기준 {LEAK_MAX:.0f} 이하) → {'통과' if ok else '빛이 샘: 옆 천 앞자락·문 윗선 틈 확인'}"
    target = LIGHT_TARGETS.get(light) if light is not None else None
    text = f"가운데 {stats.center:.1f}, 날림 {stats.clipped_pct:.3f}%"
    if target is None and center_range is not None:
        target = (*center_range, 100.0)
    if target is None:
        return True, f"{text} (기준 없음)"
    lo, hi, clip = target
    if center_range is not None:
        lo, hi = center_range
    if stats.center < lo:
        return False, f"{text} → 어두움(기준 {lo:.0f}~{hi:.0f}): 확산판 틀을 램프 쪽으로"
    if stats.center > hi:
        return False, f"{text} → 밝음(기준 {lo:.0f}~{hi:.0f}): 확산판 틀을 램프에서 멀리(가운데 쪽으로)"
    if stats.clipped_pct > clip:
        return False, f"{text} → 날림 많음(기준 {clip}% 이하): 정반사 자리, 틀 각도를 바꿔 보기"
    return True, f"{text} → 통과(기준 {lo:.0f}~{hi:.0f}, 날림 {clip}% 이하)"


def summarize(rows: list[tuple[str, LightStats]]) -> dict[str, tuple[int, float, float]]:
    """{묶음 이름: (장수, 가운데 중앙값, 날림 중앙값)}. 11/5 사진 기준을 볼 때 쓴다."""
    groups: dict[str, list[LightStats]] = {}
    for key, s in rows:
        groups.setdefault(key, []).append(s)
    return {k: (len(v), median(s.center for s in v), median(s.clipped_pct for s in v)) for k, v in sorted(groups.items())}
