"""세트의 면 사진을 탐지하고 걸린 시간을 남긴다(NFR-01: 한 세트 30초)."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..store import ShotRecord, latest_shots
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
    width: int = 0  # 원본 사진 px(화면이 축소 사진 위에 폴리곤을 겹칠 때 쓴다)
    height: int = 0


@dataclass
class SetResult:
    model_version: str
    photos: list[PhotoResult] = field(default_factory=list)
    seconds: float = 0.0

    @property
    def count(self) -> int:
        return sum(len(p.found) for p in self.photos)

    def to_json(self) -> dict[str, Any]:
        """로컬 기록용. 폴리곤은 원본 사진 px(표준 좌표로 바꾸는 일은 서버가 한다)."""
        return {
            "modelVersion": self.model_version, "seconds": self.seconds, "count": self.count,
            "coordinates": "photo_px",
            "photos": [
                {"face": p.face, "light": p.light, "camera": p.camera, "filename": p.filename,
                 "width": p.width, "height": p.height,
                 "detections": [{"class": f.defect_class, "polygon": [list(pt) for pt in f.polygon],
                                 "confidence": f.confidence} for f in p.found]}
                for p in self.photos
            ],
        }


    def detections(self) -> list[dict[str, Any]]:
        """`POST /captures/sets/{setId}/detections`의 detections. 사진마다 (face, light, camera)와 원본 px 폴리곤."""
        return [
            {"face": p.face, "light": p.light, "camera": p.camera, "class": f.defect_class,
             "polygon": [list(pt) for pt in f.polygon], "confidence": f.confidence}
            for p in self.photos for f in p.found
        ]


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
    """면 사진만(SLATE 제외), 다시 찍은 면은 마지막 장만 탐지한다(올리는 세트와 같은 사진). 원본 파일은 읽기만 한다."""
    start = clock()
    result = SetResult(info.model_version)
    for shot in latest_shots(shots):
        image = load(shot.local_path)
        found = detect_photo(image, detector, info)
        height, width = image.shape[:2]
        result.photos.append(PhotoResult(shot.face or "", shot.light or "", shot.camera, shot.filename, found,
                                         int(width), int(height)))
    result.seconds = round(clock() - start, 3)
    return result
