"""세트의 면 사진을 탐지하고 걸린 시간을 남긴다(NFR-01: 한 세트 60초)."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..codes import NON_PROBLEM_CLASSES
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

    @property
    def problem_count(self) -> int:
        """판정에 넣는 흠집 수. 오염만 서버 등급처럼 빼고, 모르는 클래스는 안전하게 문제로 센다."""
        return sum(1 for p in self.photos for f in p.found if f.defect_class not in NON_PROBLEM_CLASSES)

    @property
    def has_problem(self) -> bool:
        """부스 화면의 "문제 있음/없음". 긁힘·찍힘·균열이 하나라도 잡히면 문제 있음, 오염만이면 문제 없음(서버 등급 A와 같게)."""
        return self.problem_count > 0

    def to_json(self) -> dict[str, Any]:
        """로컬 기록용. 폴리곤은 원본 사진 px(표준 좌표로 바꾸는 일은 서버가 한다)."""
        return {
            "modelVersion": self.model_version, "seconds": self.seconds, "count": self.count,
            "problemCount": self.problem_count, "hasProblem": self.has_problem,
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


@dataclass(frozen=True)
class Photo:
    """빠른 검사 모드가 넘기는 사진 한 장. image는 OpenCV BGR 배열(높이, 너비, 3)."""

    face: str
    light: str
    camera: str
    image: Any
    filename: str = ""


def detect_photos(photos: list[Photo], detector: Detector, info: ModelInfo,
                  clock: Callable[[], float] = time.perf_counter) -> SetResult:
    """사진 몇 장(MVP: BOT·TOP × CAM1·CAM2 4장)을 탐지한다. 폴리곤은 원본 사진 px. 걸린 시간은 seconds."""
    start = clock()
    result = SetResult(info.model_version)
    for p in photos:
        height, width = p.image.shape[:2]
        result.photos.append(PhotoResult(p.face, p.light, p.camera, p.filename, detect_photo(p.image, detector, info),
                                         int(width), int(height)))
    result.seconds = round(clock() - start, 3)
    return result


def detect_photo(image: Any, detector: Detector, info: ModelInfo, size: int = 640,
                 overlap: float = 0.2) -> list[Found]:
    height, width = image.shape[:2]
    found = []
    for t in tiles(width, height, size, overlap):
        for raw in detector.infer(image[t.y:t.y + t.h, t.x:t.x + t.w]):
            found.append(Found(info.class_name(raw.class_index), to_image(raw.polygon, t, width, height),
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
