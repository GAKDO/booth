"""ONNX → TensorRT FP16 엔진(Jetson에서). trtexec를 부르고, 엔진 옆에 model.json을 둔다."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from .engine import DetectError, ModelInfo

DEFAULT_TRTEXEC = "/usr/src/tensorrt/bin/trtexec"
Runner = Callable[[list[str], Path], int]  # (명령, 로그 파일) → 종료 코드


def run_logged(cmd: list[str], log: Path) -> int:
    with log.open("w", encoding="utf-8") as f:
        return subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, check=False).returncode


def trtexec_command(onnx: Path, engine: Path, trtexec: str) -> list[str]:
    """FP16. 입력은 ONNX의 고정 크기(1x3x640x640)를 그대로 쓴다."""
    return [trtexec, f"--onnx={onnx}", f"--saveEngine={engine}", "--fp16", "--skipInference"]


def build_engine(onnx: Path, out_dir: Path, model_json: Path | None = None, test: bool = False,
                 trtexec: str | None = None, run: Runner = run_logged) -> Path:
    """엔진을 out_dir/<onnx 이름>.engine으로 만든다. model.json은 넘긴 것(또는 ONNX 옆 것)을 검사해 복사한다.

    test=True면 시험용 기본 모델(yolo11n-seg 등): 클래스 검사 없이 testOnly model.json을 쓴다(서버에 안 올림).
    """
    if not onnx.exists():
        raise DetectError(f"ONNX 파일이 없음: {onnx}")
    source = model_json or (onnx.parent / "model.json")
    if not test:
        if not source.exists():
            raise DetectError(f"model.json이 없음: {source} (시험용 기본 모델이면 --test)")
        ModelInfo.load(source)  # modelVersion·클래스가 맞지 않으면 여기서 멈춘다
    out_dir.mkdir(parents=True, exist_ok=True)
    engine = out_dir / f"{onnx.stem}.engine"
    others = [p.name for p in out_dir.glob("*.engine") if p != engine]
    if others:  # model.json은 폴더에 하나라 다른 엔진과 섞이면 클래스·modelVersion이 엇갈린다
        raise DetectError(f"{out_dir}에 다른 엔진 {others}가 있음. 모델마다 폴더를 따로 쓴다")
    tool = trtexec or os.environ.get("TRTEXEC", DEFAULT_TRTEXEC)
    if shutil.which(tool) is None:
        raise DetectError(f"trtexec를 찾지 못함: {tool} (TRTEXEC 환경 변수로 경로 지정)")
    log = out_dir / f"{onnx.stem}.trtexec.log"
    start = time.perf_counter()
    code = run(trtexec_command(onnx, engine, tool), log)
    if code != 0 or not engine.exists():
        raise DetectError(f"엔진 빌드 실패(종료 코드 {code}). 로그: {log}")
    target = out_dir / "model.json"
    if test:
        info = {"modelVersion": f"{onnx.stem}-test-trt-fp16", "classes": [], "testOnly": True}
        target.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    elif source.resolve() != target.resolve():
        shutil.copyfile(source, target)
    print(f"엔진 {engine} ({engine.stat().st_size / 1e6:.1f}MB, {time.perf_counter() - start:.0f}초), 로그 {log}")
    return engine
