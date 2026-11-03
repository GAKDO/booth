"""YOLO11-seg 출력 풀기: 레터박스 입력 만들기, 상자 NMS, 마스크 → 폴리곤(타일 px). numpy와 OpenCV만 쓴다.

ultralytics 기본 ONNX 내보내기(nms·end2end 끔) 기준:
- 입력 `images` (1, 3, 640, 640) RGB 0~1, 비율 유지 + 회색(114) 여백
- `output0` (1, 4 + 클래스 수 + 32, 8400): cx·cy·w·h(입력 px), 클래스 점수, 마스크 계수
- `output1` (1, 32, 160, 160): 마스크 원형
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from .engine import RawDetection

MASK_COEFFS = 32
MAX_DETECTIONS = 100


@dataclass(frozen=True)
class Letterbox:
    scale: float  # 타일 px × scale = 입력 px
    pad_x: float
    pad_y: float
    width: int  # 원래 타일 크기
    height: int


def letterbox(tile: Any, size: int = 640) -> tuple[np.ndarray, Letterbox]:
    """BGR(또는 회색) 타일 → (1, 3, size, size) float32 RGB 0~1. 비율을 지키고 남는 곳은 114로 채운다."""
    import cv2

    image = np.asarray(tile)
    if image.ndim == 2:
        image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    height, width = image.shape[:2]
    scale = min(size / width, size / height)
    new_w, new_h = round(width * scale), round(height * scale)
    if (new_w, new_h) != (width, height):
        image = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
    pad_x, pad_y = (size - new_w) / 2, (size - new_h) / 2
    top, left = round(pad_y - 0.1), round(pad_x - 0.1)
    canvas = np.full((size, size, 3), 114, dtype=np.uint8)
    canvas[top:top + new_h, left:left + new_w] = image
    blob = canvas[:, :, ::-1].transpose(2, 0, 1)[None].astype(np.float32) / 255.0
    return np.ascontiguousarray(blob), Letterbox(scale, float(left), float(top), width, height)


def nms(boxes: np.ndarray, scores: np.ndarray, iou: float) -> list[int]:
    """xyxy 상자의 탐욕 NMS. 점수 높은 순으로 남긴 번호."""
    order = scores.argsort()[::-1]
    area = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
    keep: list[int] = []
    while order.size:
        i = int(order[0])
        keep.append(i)
        rest = order[1:]
        ix = np.clip(np.minimum(boxes[i, 2], boxes[rest, 2]) - np.maximum(boxes[i, 0], boxes[rest, 0]), 0, None)
        iy = np.clip(np.minimum(boxes[i, 3], boxes[rest, 3]) - np.maximum(boxes[i, 1], boxes[rest, 1]), 0, None)
        inter = ix * iy
        overlap = inter / np.maximum(area[i] + area[rest] - inter, 1e-9)
        order = rest[overlap < iou]
    return keep


def _polygon(mask: np.ndarray) -> np.ndarray | None:
    """이진 마스크에서 가장 큰 바깥 윤곽. 점이 3개 안 되면 None."""
    import cv2

    contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    biggest = max(contours, key=cv2.contourArea)
    if len(biggest) < 3:
        return None
    return biggest.reshape(-1, 2).astype(np.float32)


def decode(output0: np.ndarray, output1: np.ndarray, box: Letterbox, min_confidence: float = 0.25,
           iou: float = 0.45, size: int = 640) -> list[RawDetection]:
    """엔진 출력 → 타일 px 폴리곤. 같은 클래스끼리만 NMS(ultralytics 기본과 같음)."""
    import cv2

    preds = np.asarray(output0, dtype=np.float32)[0].T  # (8400, 4 + nc + 32)
    protos = np.asarray(output1, dtype=np.float32)[0]  # (32, 160, 160)
    num_classes = preds.shape[1] - 4 - MASK_COEFFS
    if num_classes < 1:
        raise ValueError(f"YOLO-seg 출력이 아님: output0 {output0.shape}")
    class_scores = preds[:, 4:4 + num_classes]
    cls = class_scores.argmax(1)
    conf = class_scores[np.arange(len(cls)), cls]
    picked = conf >= min_confidence
    if not picked.any():
        return []
    preds, cls, conf = preds[picked], cls[picked], conf[picked]
    cx, cy, w, h = preds[:, 0], preds[:, 1], preds[:, 2], preds[:, 3]
    boxes = np.stack([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], axis=1)
    offset = cls[:, None].astype(np.float32) * (size * 4)  # 클래스마다 떨어뜨려 클래스별 NMS
    keep = nms(boxes + offset, conf, iou)[:MAX_DETECTIONS]

    proto_h, proto_w = protos.shape[1:]
    coeffs = preds[keep, 4 + num_classes:]
    masks = 1 / (1 + np.exp(-(coeffs @ protos.reshape(MASK_COEFFS, -1))))
    masks = masks.reshape(-1, proto_h, proto_w)
    ratio = proto_w / size
    ys, xs = np.arange(proto_h)[:, None], np.arange(proto_w)[None, :]
    out: list[RawDetection] = []
    for n, i in enumerate(keep):
        x0, y0, x1, y1 = boxes[i] * ratio
        inside = (xs >= x0) & (xs < x1) & (ys >= y0) & (ys < y1)  # 상자 밖 마스크는 버린다
        full = cv2.resize(masks[n] * inside, (size, size), interpolation=cv2.INTER_LINEAR) > 0.5
        poly = _polygon(full)
        if poly is None:
            continue
        px = np.clip((poly[:, 0] - box.pad_x) / box.scale, 0, box.width)
        py = np.clip((poly[:, 1] - box.pad_y) / box.scale, 0, box.height)
        out.append(RawDetection(int(cls[i]), float(conf[i]),
                                [(round(float(x), 1), round(float(y), 1)) for x, y in zip(px, py)]))
    return out
