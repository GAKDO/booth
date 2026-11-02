"""운영 화면용 로컬 HTTP API (specs/001-booth-capture-tool/contracts/operator-web-api.md)."""

from __future__ import annotations

import json
import threading
from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path

from .camera import CameraError
from .camera_presets import PresetError
from .session import CaptureError, CaptureService, Incomplete
from .store import ShotRecord


class App:
    """부스 하나에 촬영 세션 하나. 현재 세션 ID만 들고 있다."""

    def __init__(self, service: CaptureService) -> None:
        self.service = service
        self.current: int | None = None  # 내부 id. 화면에는 publicId만 보낸다
        self.lock = threading.Lock()


def _shot_json(s: ShotRecord) -> dict:
    return {
        "id": s.public_id, "filename": s.filename, "face": s.face, "light": s.light, "camera": s.camera,
        "seq": s.seq, "sha256": s.sha256, "uploadStatus": s.upload_status,
    }


def _state(app: App) -> dict:
    v = app.service.view(app.current)
    step = None
    if v.step is not None:
        step = {
            "index": v.step.index, "kind": v.step.kind.value, "instruction": v.step.instruction,
            "face": v.step.face.value if v.step.face else None,
            "light": v.step.light.value if v.step.light else None,
            "cameras": [c.value for c in v.step.cameras], "optional": v.step.optional,
        }
    return {
        "sessionId": v.session.public_id, "deviceId": v.session.device_id, "mode": v.session.mode,
        "status": v.session.status, "step": step, "remainingSteps": v.remaining_steps,
        "missing": v.missing, "scr1SkipReason": v.session.scr1_skip_reason,
        "queue": app.service.store.upload_counts(), "presetsConfigured": app.service.presets.configured,
    }


def make_handler(app: App) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: object) -> None:  # 요청 로그는 남기지 않음
            pass

        def do_GET(self) -> None:
            self._dispatch("GET")

        def do_POST(self) -> None:
            self._dispatch("POST")

        def _dispatch(self, method: str) -> None:
            try:
                self._route(method, self.path.split("?", 1)[0])
            except Incomplete as e:
                self._problem(409, str(e), missing=e.missing)
            except CaptureError as e:
                self._problem(e.status, str(e))
            except CameraError as e:
                self._problem(503, f"카메라 실패: {e.reason}", camera=e.camera.value)
            except PresetError as e:
                self._problem(500, str(e))
            except (ValueError, KeyError, TypeError) as e:
                self._problem(400, f"잘못된 요청: {e}")

        def _route(self, method: str, path: str) -> None:
            svc = app.service
            if method == "GET" and path == "/":
                body = resources.files("trace_booth").joinpath("static/index.html").read_bytes()
                return self._send(200, body, "text/html; charset=utf-8")
            if method == "GET" and path == "/api/queue":
                return self._json(200, svc.store.upload_counts())
            if method == "GET" and path.startswith("/api/shots/") and path.endswith("/thumbnail"):
                shot = svc.store.get_shot(path.split("/")[3])
                if shot is None:
                    return self._problem(404, "사진이 없음")
                return self._send(200, _thumbnail(Path(shot.local_path)), "image/jpeg")
            if method == "POST" and path == "/api/sessions":
                body = self._body()
                with app.lock:
                    app.current = svc.start(str(body.get("deviceId", "")), str(body.get("mode", "TRAINING"))).id
                return self._json(201, _state(app))
            if not path.startswith("/api/sessions/current"):
                return self._problem(404, "없는 경로")
            if app.current is None:
                return self._problem(404, "시작한 촬영이 없음")
            action = path[len("/api/sessions/current"):]
            if method == "GET" and action == "":
                return self._json(200, _state(app))
            if method != "POST":
                return self._problem(405, "허용하지 않는 메서드")
            if action == "/shoot":
                shots = svc.shoot(app.current)
                return self._json(201, {"shots": [_shot_json(s) for s in shots], "state": _state(app)})
            if action == "/retake":
                shots = svc.retake(app.current)
                return self._json(201, {"shots": [_shot_json(s) for s in shots], "state": _state(app)})
            if action == "/next":
                svc.acknowledge(app.current)
                return self._json(200, _state(app))
            if action == "/skip-scr1":
                svc.skip_optional(app.current, str(self._body().get("reason", "")))
                return self._json(200, _state(app))
            if action == "/complete":
                done = svc.complete(app.current)
                return self._json(200, {"session": asdict(done)})
            return self._problem(404, "없는 경로")

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            data = json.loads(self.rfile.read(n) or b"{}")
            if not isinstance(data, dict):
                raise TypeError("JSON 객체가 아님")
            return data

        def _json(self, status: int, data: dict) -> None:
            self._send(status, json.dumps(data, ensure_ascii=False).encode(), "application/json")

        def _problem(self, status: int, detail: str, **extra: object) -> None:
            body = {"type": "about:blank", "title": HTTPStatus(status).phrase, "status": status, "detail": detail}
            body.update(extra)
            self._send(status, json.dumps(body, ensure_ascii=False).encode(), "application/problem+json")

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    return Handler


def _thumbnail(path: Path, long_side: int = 480) -> bytes:
    data = path.read_bytes()
    try:
        import cv2
        import numpy as np
    except ImportError:
        return data
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return data
    h, w = img.shape[:2]
    scale = long_side / max(h, w)
    if scale < 1:
        img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return buf.tobytes() if ok else data


def make_server(app: App, host: str, port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), make_handler(app))
