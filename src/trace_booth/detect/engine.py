"""탐지기 프로토콜과 모델 정보. TensorRT 엔진 추론은 Jetson에서만 ultralytics를 늦게 불러 쓴다."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ..codes import DEFECT_CLASSES


class DetectError(Exception):
    """탐지를 시작할 수 없음(엔진·모델 정보 없음, 클래스 불일치)."""


@dataclass(frozen=True)
class RawDetection:
    class_index: int
    confidence: float
    polygon: list[tuple[float, float]]  # 타일 px


class Detector(Protocol):
    def infer(self, tile: Any) -> list[RawDetection]: ...


@dataclass(frozen=True)
class ModelInfo:
    model_version: str
    classes: tuple[str, ...]

    @classmethod
    def load(cls, path: Path) -> ModelInfo:
        """엔진 옆 model.json. modelVersion이 없거나 클래스가 codes.yaml과 다르면 시작하지 않는다."""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DetectError(f"모델 정보를 읽지 못함: {path}") from exc
        version = str(data.get("modelVersion") or "").strip()
        if not version:
            raise DetectError("model.json에 modelVersion이 없음")
        classes = tuple(data.get("classes") or ())
        if set(classes) != set(DEFECT_CLASSES) or len(classes) != len(DEFECT_CLASSES):
            raise DetectError(f"모델 클래스 {list(classes)}가 codes.yaml {list(DEFECT_CLASSES)}와 다름")
        return cls(version, classes)


class UltralyticsDetector:
    """TensorRT FP16 엔진(.engine)을 ultralytics로 불러 추론한다. Jetson에서만 쓴다."""

    def __init__(self, engine_path: Path, size: int = 640, min_confidence: float = 0.25) -> None:
        if not engine_path.exists():
            raise DetectError(f"엔진 파일이 없음: {engine_path}")
        try:
            from ultralytics import YOLO  # Jetson 전용 선택 의존성
        except ImportError as exc:
            raise DetectError("ultralytics가 설치되지 않음(pip install -e '.[detect]')") from exc
        self._model = YOLO(str(engine_path), task="segment")
        self._size = size
        self._conf = min_confidence

    def infer(self, tile: Any) -> list[RawDetection]:
        result = self._model.predict(tile, imgsz=self._size, conf=self._conf, half=True, verbose=False)[0]
        if result.masks is None:
            return []
        return [
            RawDetection(int(c), float(p), [(float(x), float(y)) for x, y in poly])
            for c, p, poly in zip(result.boxes.cls.tolist(), result.boxes.conf.tolist(), result.masks.xy)
            if len(poly) >= 3
        ]
