"""명령줄 진입점. `trace-booth serve`로 부스 촬영 화면을 띄운다."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import __version__
from .camera import OpenCvCamera
from .camera_presets import Presets
from .capture_client import BoothAuth, CaptureClient
from .checkout import submit_session
from .codes import Camera
from .session import CaptureService, _now
from .store import Store
from .uploader import S3ObjectStore, UploadWorker, object_key
from .web import App, Submitter, make_server


def _env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None:
        raise SystemExit(f"환경 변수 {name}가 필요해요")
    return value


def _data_dir() -> Path:
    data_dir = Path(_env("TRACE_BOOTH_DATA_DIR", str(Path.home() / "trace-booth-data"))).expanduser()
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def _object_store() -> S3ObjectStore | None:
    """MINIO_ENDPOINT가 없으면 업로드 없이 로컬에만 남긴다."""
    if not os.environ.get("MINIO_ENDPOINT"):
        return None
    return S3ObjectStore(
        _env("MINIO_ENDPOINT"), _env("MINIO_ACCESS_KEY"), _env("MINIO_SECRET_KEY"), _env("TRACE_RAW_BUCKET", "trace-raw")
    )


def _submitter(store: Store) -> Submitter | None:
    """출고·반납 모드용. TRACE_API_URL(gateway)과 부스 자격(값은 trace-secrets)이 없으면 None."""
    url = os.environ.get("TRACE_API_URL")
    if not url:
        return None
    auth = BoothAuth(url, _env("TRACE_BOOTH_CLIENT_ID"), _env("TRACE_BOOTH_CLIENT_SECRET"))
    client = CaptureClient(url, auth)
    return lambda session_id: submit_session(store, session_id, client, _now)


def serve(host: str, port: int) -> int:
    data_dir = _data_dir()
    presets_path = os.environ.get("TRACE_BOOTH_PRESETS")
    presets = Presets.load(Path(presets_path).expanduser()) if presets_path else Presets()
    if not presets.configured:
        print("경고: TRACE_BOOTH_PRESETS가 없어 카메라 고정 설정 없이 찍어요")
    cameras = {
        Camera.CAM1: OpenCvCamera(Camera.CAM1, _env("TRACE_BOOTH_CAM1")),
        Camera.CAM2: OpenCvCamera(Camera.CAM2, _env("TRACE_BOOTH_CAM2")),
    }
    store = Store(data_dir / "booth.db")
    service = CaptureService(store, cameras, presets, data_dir)
    objects = _object_store()
    if objects is None:
        print("경고: MINIO_ENDPOINT가 없어 사진을 올리지 않고 로컬에만 남겨요")
    else:
        UploadWorker(store, objects).start()
    submitter = _submitter(store)
    if submitter is None:
        print("경고: TRACE_API_URL이 없어 출고·반납 모드를 쓸 수 없어요(학습 데이터 모드만)")
    server = make_server(App(service, submitter), host, port)
    print(f"trace-booth {__version__}: http://{host}:{port} (데이터 {data_dir})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def shots(device_id: str, verify: bool) -> int:
    """기기의 사진 목록과 업로드 상태. --verify면 저장소의 해시와 비교한다."""
    store = Store(_data_dir() / "booth.db")
    objects = _object_store() if verify else None
    if verify and objects is None:
        raise SystemExit("--verify에는 MINIO_ENDPOINT가 필요해요")
    bad = 0
    for shot in store.shots_for_device(device_id):
        line = f"{shot.filename}\t{shot.upload_status}\t{shot.sha256[:12]}"
        if objects is not None:
            meta = objects.head(object_key(shot))
            ok = meta is not None and meta.get("sha256") == shot.sha256
            bad += not ok
            line += "\t일치" if ok else "\t불일치·없음"
        print(line)
    return 1 if bad else 0


def retry() -> int:
    """대기열을 지금 한 번 올린다."""
    objects = _object_store()
    if objects is None:
        raise SystemExit("MINIO_ENDPOINT가 필요해요")
    uploaded, failed = UploadWorker(Store(_data_dir() / "booth.db"), objects).run_once()
    print(f"올림 {uploaded}장, 실패 {failed}장")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="trace-booth")
    parser.add_argument("--version", action="store_true", help="버전만 출력")
    sub = parser.add_subparsers(dest="command")
    p_serve = sub.add_parser("serve", help="운영 화면(웹)을 띄운다")
    p_serve.add_argument("--host", default="0.0.0.0")
    p_serve.add_argument("--port", type=int, default=8320)
    p_shots = sub.add_parser("shots", help="기기의 사진 목록과 업로드 상태")
    p_shots.add_argument("--device", required=True)
    p_shots.add_argument("--verify", action="store_true", help="저장소 해시와 비교")
    sub.add_parser("retry", help="올리지 못한 사진을 지금 올린다")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if args.command == "serve":
        return serve(args.host, args.port)
    if args.command == "shots":
        return shots(args.device, args.verify)
    if args.command == "retry":
        return retry()
    print(f"trace-booth {__version__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
