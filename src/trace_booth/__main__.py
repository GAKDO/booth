"""명령줄 진입점. `trace-booth serve`로 부스 촬영 화면을 띄운다."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable
from pathlib import Path

from . import __version__
from .camera import OpenCvCamera
from .camera_presets import Presets
from .capture_client import ApiError, BoothAuth, CaptureClient
from .checkout import submit_session
from .codes import Camera
from .detect.engine import DetectError, Detector, ModelInfo, UltralyticsDetector
from .detect.runner import detect_set
from .live import CameraGate, OpenCvLiveSource, live_frames
from .session import CaptureService, _now
from .store import Store
from .uploader import S3ObjectStore, UploadWorker, object_key
from .web import App, LiveFeed, Submitter, make_server


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


def _client() -> CaptureClient | None:
    """capture-server 호출용. TRACE_API_URL(gateway)과 부스 자격(값은 trace-secrets)이 없으면 None."""
    url = os.environ.get("TRACE_API_URL")
    if not url:
        return None
    auth = BoothAuth(url, _env("TRACE_BOOTH_CLIENT_ID"), _env("TRACE_BOOTH_CLIENT_SECRET"))
    return CaptureClient(url, auth)


def _submitter(store: Store) -> Submitter | None:
    """출고·반납 모드용. 서버 설정이 없으면 None."""
    client = _client()
    if client is None:
        return None
    return lambda session_id: submit_session(store, session_id, client, _now)


def _live_feed(code: Camera, device: str, gate: CameraGate) -> LiveFeed:
    return lambda: live_frames(lambda: OpenCvLiveSource(code, device), gate)


def serve(host: str, port: int) -> int:
    data_dir = _data_dir()
    presets_path = os.environ.get("TRACE_BOOTH_PRESETS")
    presets = Presets.load(Path(presets_path).expanduser()) if presets_path else Presets()
    if not presets.configured:
        print("경고: TRACE_BOOTH_PRESETS가 없어 카메라 고정 설정 없이 찍어요")
    gate = CameraGate()  # 촬영과 실시간 보기가 장치를 나눠 쓴다
    devices = {Camera.CAM1: _env("TRACE_BOOTH_CAM1"), Camera.CAM2: _env("TRACE_BOOTH_CAM2")}
    cameras = {code: OpenCvCamera(code, dev, gate=gate) for code, dev in devices.items()}
    live = {code.value: _live_feed(code, dev, gate) for code, dev in devices.items()}
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
    server = make_server(App(service, submitter, live), host, port)
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


NFR_DETECT_SECONDS = 30


def _imread(path: str) -> object:
    import cv2

    return cv2.imread(path, cv2.IMREAD_COLOR)


def detect(session_public_id: str, engine: str | None, make_detector: Callable[[Path], Detector] | None = None,
           load: Callable[[str], object] = _imread, client: CaptureClient | None = None) -> int:
    """찍은 세션 하나의 면 사진을 탐지해 로컬에 JSON으로 남긴다(BO2). 원본 사진은 읽기만 한다.

    출고·반납 세트가 이미 올라갔으면 탐지 결과도 그 세트에 올린다. 다시 돌리면 다시 보내고, 서버가 그 세트의
    부스 탐지를 마지막 요청으로 통째로 바꾼다(A 11/2). 종료 코드: 30초 초과 2, 올리기 실패 3.
    """
    import json

    data_dir = _data_dir()
    store = Store(data_dir / "booth.db")
    session = store.find_session(session_public_id)
    if session is None:
        raise SystemExit(f"세션이 없어요: {session_public_id}")
    engine_path = Path(engine or _env("TRACE_BOOTH_ENGINE")).expanduser()
    try:
        info = ModelInfo.load(engine_path.parent / "model.json")
        detector = (make_detector or UltralyticsDetector)(engine_path)
    except DetectError as e:
        raise SystemExit(f"탐지를 시작할 수 없어요: {e}") from e
    result = detect_set(store.shots(session.id), detector, info, load)
    out = data_dir / session.device_id / "detections" / f"{session.public_id}_{info.model_version}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result.to_json(), ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"사진 {len(result.photos)}장, 흠집 {result.count}개, {result.seconds}초 → {out}")
    code = 0
    if result.seconds > NFR_DETECT_SECONDS:
        print(f"경고: 한 세트 {NFR_DETECT_SECONDS}초를 넘었어요")
        code = 2
    record = store.get_checkout(session.id)
    if record is None or record.status != "SUBMITTED" or record.capture_set_id is None:
        print("올린 출고·반납 세트가 없어 탐지 결과는 로컬에만 남겨요")
        return code
    client = client or _client()
    if client is None:
        print("TRACE_API_URL이 없어 탐지 결과는 로컬에만 남겨요")
        return code
    try:
        accepted = client.save_detections(record.capture_set_id, info.model_version, result.detections())
    except ApiError as e:
        if e.code == "CAPTURE_SET_CLOSED":
            print(f"세트 {record.capture_set_id}는 이미 등급이 매겨져 탐지를 바꿀 수 없어요(로컬 JSON은 남음)")
        else:
            print(f"탐지 결과를 올리지 못했어요(로컬 JSON은 남음): {e}")
        return 3
    except OSError as e:
        print(f"탐지 결과를 올리지 못했어요(로컬 JSON은 남음): {e}")
        return 3
    print(f"세트 {record.capture_set_id}에 탐지 {accepted}개를 올렸어요")
    return code


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
    p_detect = sub.add_parser("detect", help="세션 하나의 면 사진을 탐지해 로컬 JSON으로 남긴다(Jetson)")
    p_detect.add_argument("--session", required=True, help="세션 publicId(운영 화면의 sessionId)")
    p_detect.add_argument("--engine", help="TensorRT 엔진(.engine). 같은 폴더에 model.json. 기본 TRACE_BOOTH_ENGINE")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if args.command == "serve":
        return serve(args.host, args.port)
    if args.command == "shots":
        return shots(args.device, args.verify)
    if args.command == "retry":
        return retry()
    if args.command == "detect":
        return detect(args.session, args.engine)
    print(f"trace-booth {__version__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
