"""운영 화면용 로컬 HTTP API (specs/001-booth-capture-tool/contracts/operator-web-api.md)."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Iterator
from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path

from .camera import CameraError
from .camera_presets import PresetError
from .capture_client import ApiError
from .quick import QuickInspector, result_json
from .rentals import WaitingDevice
from .session import CaptureError, CaptureService, Incomplete, InvalidInput
from .store import CheckoutRecord, ShotRecord

Submitter = Callable[[int], CheckoutRecord]
LiveFeed = Callable[[], Iterator[bytes]]  # 카메라 하나의 실시간 JPEG 프레임
WaitingList = Callable[[], list[WaitingDevice]]  # 빠른 검사의 검사 기다리는 기기


class App:
    """부스 하나에 촬영 세션 하나. 현재 세션 ID만 들고 있다."""

    def __init__(
        self,
        service: CaptureService,
        submitter: Submitter | None = None,
        live: dict[str, LiveFeed] | None = None,
        quick: QuickInspector | None = None,
        waiting: WaitingList | None = None,
    ) -> None:
        self.service = service
        self.live = live or {}  # 카메라 코드(CAM1·CAM2) → 실시간 보기
        self.submitter = submitter  # 출고·반납 세트 올리기. 서버 설정이 없으면 None(출고·반납 모드를 막음)
        self.quick = quick  # 빠른 검사 탐지. 없으면 QUICK 모드를 막음
        self.waiting = waiting  # 렌탈 목록. 서버 설정이 없으면 None
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
        "checkout": _checkout_json(app.service.store.get_checkout(v.session.id)),
        "checkoutEnabled": app.submitter is not None,
        "quickEnabled": app.quick is not None, "waitingEnabled": app.waiting is not None,
    }


def _checkout_json(c: CheckoutRecord | None) -> dict | None:
    if c is None:
        return None
    return {
        "rentalId": c.rental_id, "checkpointId": c.checkpoint_id, "captureSetId": c.capture_set_id,
        "status": c.status, "attempts": c.attempts, "lastError": c.last_error,
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
            if method == "GET" and path == "/api/rentals/waiting":
                if app.waiting is None:
                    return self._problem(503, "서버 연결 설정(TRACE_API_URL·부스 자격)이 없어 기기 목록을 못 불러와요")
                try:
                    items = app.waiting()
                except (ApiError, OSError) as e:
                    return self._problem(502, f"기기 목록을 못 불러왔어요: {e}")
                return self._json(200, {"items": [w.to_json() for w in items]})
            if method == "GET" and path.startswith("/api/live/"):
                feed = app.live.get(path[len("/api/live/"):])
                if feed is None:
                    return self._problem(404, "실시간 보기를 쓸 수 없는 카메라")
                return self._stream(feed())
            if method == "POST" and path == "/api/sessions":
                body = self._body()
                mode = str(body.get("mode", "TRAINING"))
                if mode == "QUICK" and app.quick is None:
                    raise InvalidInput("탐지 엔진 설정(TRACE_BOOTH_ENGINE)이 없어 빠른 검사를 쓸 수 없어요")
                if mode in ("OUT", "RET") and app.submitter is None:
                    raise InvalidInput("서버 연결 설정(TRACE_API_URL·부스 자격)이 없어 출고·반납 모드를 쓸 수 없어요")
                rental, checkpoint = body.get("rentalId"), body.get("checkpointId")
                with app.lock:
                    app.current = svc.start(
                        str(body.get("deviceId", "")), mode,
                        str(rental) if rental else None, str(checkpoint) if checkpoint else None,
                    ).id
                return self._json(201, _state(app))
            if not path.startswith("/api/sessions/current"):
                return self._problem(404, "없는 경로")
            if app.current is None:
                return self._problem(404, "시작한 촬영이 없음")
            action = path[len("/api/sessions/current"):]
            if method == "GET" and action == "":
                return self._json(200, _state(app))
            if method == "GET" and action == "/result":
                session = svc.store.get_session(app.current)
                if session.mode != "QUICK":
                    return self._problem(409, "빠른 검사 촬영이 아니에요")
                return self._json(200, result_json(svc.store, session.id, session.status == "IN_PROGRESS"))
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
                if done.mode == "QUICK" and app.quick is not None:
                    app.quick.start(done.id)  # 바로 돌아오고, 화면은 /result를 다시 읽는다
                elif done.mode != "TRAINING" and app.submitter is not None:
                    app.submitter(done.id)
                return self._json(200, {"session": asdict(done), "state": _state(app)})
            if action == "/detect":
                session = svc.store.get_session(app.current)
                if session.mode != "QUICK" or session.status != "COMPLETE" or app.quick is None:
                    raise CaptureError("완료한 빠른 검사만 다시 탐지할 수 있어요")
                app.quick.start(session.id)
                return self._json(202, result_json(svc.store, session.id, False))
            if action == "/submit":
                if app.submitter is None:
                    raise InvalidInput("서버 연결 설정이 없어요")
                app.submitter(app.current)
                return self._json(200, _state(app))
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

        def _stream(self, frames: Iterator[bytes]) -> None:
            """multipart/x-mixed-replace로 JPEG를 이어 보낸다. 받는 쪽이 끊으면 장치를 닫는다."""
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                for jpg in frames:
                    self.wfile.write(
                        b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                        + str(len(jpg)).encode() + b"\r\n\r\n" + jpg + b"\r\n"
                    )
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                close = getattr(frames, "close", None)
                if close is not None:
                    close()
            self.close_connection = True

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
