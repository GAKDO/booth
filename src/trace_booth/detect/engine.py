"""탐지기 프로토콜과 모델 정보, TensorRT 탐지기. tensorrt·CUDA는 Jetson에서만 늦게 불러 쓴다(torch 없이)."""

from __future__ import annotations

import json
import time
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


@dataclass
class Stats:
    """탐지기 안 구간 시간(초) 누적. bench가 어디가 느린지(입력 만들기·엔진·후처리) 보여 줄 때 쓴다."""

    tiles: int = 0
    prep: float = 0.0
    run: float = 0.0
    post: float = 0.0

    def reset(self) -> None:
        self.tiles, self.prep, self.run, self.post = 0, 0.0, 0.0, 0.0


class Detector(Protocol):
    def infer(self, tile: Any) -> list[RawDetection]: ...


@dataclass(frozen=True)
class ModelInfo:
    model_version: str
    classes: tuple[str, ...]
    test_only: bool = False  # 시험용 기본 모델(예: COCO yolo11n-seg). 클래스 검사를 건너뛰고 서버에 올리지 않는다

    @classmethod
    def load(cls, path: Path) -> ModelInfo:
        """엔진 옆 model.json. modelVersion이 없거나 클래스가 codes.yaml과 다르면 시작하지 않는다(testOnly 제외)."""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DetectError(f"모델 정보를 읽지 못함: {path}") from exc
        version = str(data.get("modelVersion") or "").strip()
        if not version:
            raise DetectError("model.json에 modelVersion이 없음")
        classes = tuple(data.get("classes") or ())
        if data.get("testOnly") is True:
            return cls(version, classes, test_only=True)
        if set(classes) != set(DEFECT_CLASSES) or len(classes) != len(DEFECT_CLASSES):
            raise DetectError(f"모델 클래스 {list(classes)}가 codes.yaml {list(DEFECT_CLASSES)}와 다름")
        return cls(version, classes)

    def class_name(self, index: int) -> str:
        return self.classes[index] if index < len(self.classes) else f"class{index}"


class _Cuda:
    """libcudart를 ctypes로 부른다(Jetson에 torch·pycuda가 없어도 되게). 쓰는 함수는 5개뿐."""

    H2D, D2H = 1, 2

    def __init__(self) -> None:
        import ctypes

        self._c = ctypes
        for name in ("libcudart.so", "libcudart.so.13", "libcudart.so.12"):
            try:
                self._lib = ctypes.CDLL(name)
                break
            except OSError:
                continue
        else:
            raise DetectError("libcudart를 찾지 못함(/usr/local/cuda/lib64가 LD_LIBRARY_PATH에 있는지)")

    def _check(self, code: int, what: str) -> None:
        if code != 0:
            raise DetectError(f"CUDA {what} 실패(코드 {code})")

    def malloc(self, nbytes: int) -> int:
        ptr = self._c.c_void_p()
        self._check(self._lib.cudaMalloc(self._c.byref(ptr), self._c.c_size_t(nbytes)), "cudaMalloc")
        return int(ptr.value or 0)

    def stream(self) -> int:
        s = self._c.c_void_p()
        self._check(self._lib.cudaStreamCreate(self._c.byref(s)), "cudaStreamCreate")
        return int(s.value or 0)

    def copy(self, dst: int, src: int, nbytes: int, kind: int) -> None:
        self._check(self._lib.cudaMemcpy(self._c.c_void_p(dst), self._c.c_void_p(src), self._c.c_size_t(nbytes),
                                         self._c.c_int(kind)), "cudaMemcpy")

    def sync(self, stream: int) -> None:
        self._check(self._lib.cudaStreamSynchronize(self._c.c_void_p(stream)), "cudaStreamSynchronize")


class TensorRTDetector:
    """trtexec로 만든 FP16 엔진(.engine)을 TensorRT 파이썬으로 돌린다. Jetson에서만 쓴다.

    입력 1개(1, 3, 640, 640), 출력 2개(3차원 = 상자·점수·계수, 4차원 = 마스크 원형). 한 번 만들어 두고 계속 쓴다
    (엔진을 올리는 데 몇 초 걸린다).
    """

    def __init__(self, engine_path: Path, min_confidence: float = 0.25, iou: float = 0.45) -> None:
        if not engine_path.exists():
            raise DetectError(f"엔진 파일이 없음: {engine_path}")
        try:
            import tensorrt as trt
        except ImportError as exc:
            raise DetectError("tensorrt 파이썬이 없음(JetPack 시스템 패키지, 가상환경은 --system-site-packages)") from exc
        import numpy as np

        self._np = np
        self._cuda = _Cuda()
        logger = trt.Logger(trt.Logger.WARNING)
        self._engine = trt.Runtime(logger).deserialize_cuda_engine(engine_path.read_bytes())
        if self._engine is None:
            raise DetectError(f"엔진을 읽지 못함(이 기기에서 만든 엔진인지): {engine_path}")
        self._context = self._engine.create_execution_context()
        self._stream = self._cuda.stream()
        self._host: dict[str, Any] = {}
        self._device: dict[str, int] = {}
        self._input = ""
        self._outputs: list[str] = []
        for i in range(self._engine.num_io_tensors):
            name = self._engine.get_tensor_name(i)
            shape = tuple(self._engine.get_tensor_shape(name))
            if any(d < 0 for d in shape):
                raise DetectError(f"고정 입력 엔진이 아님({name} {shape}): ONNX를 dynamic=False로 내보낸다")
            host = np.empty(shape, dtype=trt.nptype(self._engine.get_tensor_dtype(name)))
            self._host[name] = host
            self._device[name] = self._cuda.malloc(host.nbytes)
            self._context.set_tensor_address(name, self._device[name])
            if self._engine.get_tensor_mode(name) == trt.TensorIOMode.INPUT:
                self._input = name
            else:
                self._outputs.append(name)
        self._outputs.sort(key=lambda n: self._host[n].ndim)  # [상자·점수(3차원), 마스크 원형(4차원)]
        self.size = int(self._host[self._input].shape[-1])
        self._conf = min_confidence
        self._iou = iou
        self.stats = Stats()

    def raw(self, blob: Any) -> tuple[Any, Any]:
        np = self._np
        host_in = self._host[self._input]
        np.copyto(host_in, blob.astype(host_in.dtype, copy=False))
        self._cuda.copy(self._device[self._input], host_in.ctypes.data, host_in.nbytes, _Cuda.H2D)
        if not self._context.execute_async_v3(self._stream):
            raise DetectError("TensorRT 추론 실패")
        self._cuda.sync(self._stream)
        for name in self._outputs:
            out = self._host[name]
            self._cuda.copy(out.ctypes.data, self._device[name], out.nbytes, _Cuda.D2H)
        return self._host[self._outputs[0]], self._host[self._outputs[1]]

    def infer(self, tile: Any) -> list[RawDetection]:
        from .yolo_seg import decode, letterbox

        t0 = time.perf_counter()
        blob, box = letterbox(tile, self.size)
        t1 = time.perf_counter()
        out0, out1 = self.raw(blob)
        t2 = time.perf_counter()
        found = decode(out0, out1, box, self._conf, self._iou, self.size)
        _add(self.stats, t0, t1, t2, time.perf_counter())
        return found


class OnnxRuntimeDetector:
    """같은 ONNX를 CPU(onnxruntime)로 돌린다. GPU 없는 PC에서 결과를 맞춰 볼 때만. 느리다."""

    def __init__(self, onnx_path: Path, min_confidence: float = 0.25, iou: float = 0.45) -> None:
        if not onnx_path.exists():
            raise DetectError(f"ONNX 파일이 없음: {onnx_path}")
        try:
            import onnxruntime as ort
        except ImportError as exc:
            raise DetectError("onnxruntime이 설치되지 않음(pip install onnxruntime)") from exc
        self._session = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
        self._input = self._session.get_inputs()[0].name
        self.size = int(self._session.get_inputs()[0].shape[-1])
        self._conf = min_confidence
        self._iou = iou
        self.stats = Stats()

    def infer(self, tile: Any) -> list[RawDetection]:
        from .yolo_seg import decode, letterbox

        t0 = time.perf_counter()
        blob, box = letterbox(tile, self.size)
        t1 = time.perf_counter()
        out0, out1 = sorted(self._session.run(None, {self._input: blob}), key=lambda a: a.ndim)
        t2 = time.perf_counter()
        found = decode(out0, out1, box, self._conf, self._iou, self.size)
        _add(self.stats, t0, t1, t2, time.perf_counter())
        return found


def _add(stats: Stats, t0: float, t1: float, t2: float, t3: float) -> None:
    stats.tiles += 1
    stats.prep += t1 - t0
    stats.run += t2 - t1
    stats.post += t3 - t2


def open_detector(path: Path) -> Detector:
    """.engine이면 TensorRT(Jetson), .onnx면 onnxruntime(CPU 확인용)."""
    if path.suffix == ".onnx":
        return OnnxRuntimeDetector(path)
    return TensorRTDetector(path)
