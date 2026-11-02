"""명령줄 진입점. `trace-booth serve`로 부스 촬영 화면을 띄운다."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import __version__
from .camera import OpenCvCamera
from .camera_presets import Presets
from .codes import Camera
from .session import CaptureService
from .store import Store
from .web import App, make_server


def _env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None:
        raise SystemExit(f"환경 변수 {name}가 필요해요")
    return value


def serve(host: str, port: int) -> int:
    data_dir = Path(_env("TRACE_BOOTH_DATA_DIR", str(Path.home() / "trace-booth-data"))).expanduser()
    data_dir.mkdir(parents=True, exist_ok=True)
    presets_path = os.environ.get("TRACE_BOOTH_PRESETS")
    presets = Presets.load(Path(presets_path).expanduser()) if presets_path else Presets()
    if not presets.configured:
        print("경고: TRACE_BOOTH_PRESETS가 없어 카메라 고정 설정 없이 찍어요")
    cameras = {
        Camera.CAM1: OpenCvCamera(Camera.CAM1, _env("TRACE_BOOTH_CAM1")),
        Camera.CAM2: OpenCvCamera(Camera.CAM2, _env("TRACE_BOOTH_CAM2")),
    }
    service = CaptureService(Store(data_dir / "booth.db"), cameras, presets, data_dir)
    server = make_server(App(service), host, port)
    print(f"trace-booth {__version__}: http://{host}:{port} (데이터 {data_dir})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="trace-booth")
    parser.add_argument("--version", action="store_true", help="버전만 출력")
    sub = parser.add_subparsers(dest="command")
    p_serve = sub.add_parser("serve", help="운영 화면(웹)을 띄운다")
    p_serve.add_argument("--host", default="0.0.0.0")
    p_serve.add_argument("--port", type=int, default=8320)
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if args.command == "serve":
        return serve(args.host, args.port)
    print(f"trace-booth {__version__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
