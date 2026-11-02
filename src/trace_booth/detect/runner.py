"""세트의 면 사진을 탐지하고 걸린 시간을 남긴다(NFR-01: 한 세트 30초)."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..store import ShotRecord
from .engine import Detector, ModelInfo
from .merge import Found, dedupe
from .tiling import tiles, to_image

ImageLoader = Callable[[str], Any]  # 경로 → (높이, 너비, 3) 배열


@dataclass
class PhotoResult:
    face: str
    light: str
    camera: str
    filename: str
    found: list[Found]


@dataclass
class SetResult:
    model_version: str
    photos: list[PhotoResult] = field(default_factory=list)
    seconds: float = 0.0

    @property
    def count(self) -> int:
        return sum(len(p.found) for p in self.photos)

    def to_json(self) -> dict[str, Any]:
        """로컬 기록용. 폴리곤은 원본 사진 px(계약의 표준 좌표가 아님: 올리기는 A의 답 뒤 T009)."""
        return {
            "modelVersion": self.model_version, "seconds": self.seconds, "count": self.count,
            "coordinates": "photo_px",
            "photos": [
                {"face": p.face, "light": p.light, "camera": p.camera, "filename": p.filename,
                 "detections": [{"class": f.defect_class, "polygon": [list(pt) for pt in f.polygon],
                                 "confidence": f.confidence} for f in p.found]}
                for p in self.photos
            ],
        }


def detect_photo(image: Any, detector: Detector, info: ModelInfo, size: int = 640,
                 overlap: float = 0.2) -> list[Found]:
    height, width = image.shape[:2]
    found = []
    for t in tiles(width, height, size, overlap):
        for raw in detector.infer(image[t.y:t.y + t.h, t.x:t.x + t.w]):
            found.append(Found(info.classes[raw.class_index], to_image(raw.polygon, t, width, height),
                               round(raw.confidence, 4)))
    return dedupe(found)


def detect_set(shots: list[ShotRecord], detector: Detector, info: ModelInfo, load: ImageLoader,
               clock: Callable[[], float] = time.perf_counter) -> SetResult:
    """면 사진만(SLATE 제외) 탐지한다. 원본 파일은 읽기만 한다."""
    start = clock()
    result = SetResult(info.model_version)
    for shot in shots:
        if shot.kind != "SHOT" or shot.face is None or shot.light is None:
            continue
        found = detect_photo(load(shot.local_path), detector, info)
        result.photos.append(PhotoResult(shot.face, shot.light, shot.camera, shot.filename, found))
    result.seconds = round(clock() - start, 3)
    return result
