"""실시간 카메라 보기: 촬영과 장치 나눠 쓰기, MJPEG 응답."""

import threading
import urllib.request

from trace_booth.camera import CameraError
from trace_booth.codes import Camera
from trace_booth.live import CameraGate, live_frames
from trace_booth.web import App, make_server


class FakeSource:
    opened = 0
    closed = 0

    def __init__(self) -> None:
        FakeSource.opened += 1
        self.n = 0

    def read(self) -> bytes | None:
        self.n += 1
        return b"\xff\xd8" + str(self.n).encode() + b"\xff\xd9"

    def close(self) -> None:
        FakeSource.closed += 1


def _reset() -> None:
    FakeSource.opened = FakeSource.closed = 0


def test_실시간_보기는_프레임을_이어서_낸다():
    _reset()
    frames = live_frames(FakeSource, CameraGate(), sleep=lambda _: None)
    got = [next(frames) for _ in range(3)]
    assert got[0].startswith(b"\xff\xd8") and len(set(got)) == 3
    frames.close()
    assert FakeSource.opened == 1 and FakeSource.closed == 1


def test_촬영이_시작되면_장치를_닫고_끝나면_다시_연다():
    _reset()
    gate = CameraGate()
    frames = live_frames(FakeSource, gate, sleep=lambda _: None)
    next(frames)
    entered = threading.Event()
    release = threading.Event()

    def shoot() -> None:
        with gate.exclusive(timeout=2):
            entered.set()
            release.wait(2)

    t = threading.Thread(target=shoot)
    t.start()
    # 촬영이 기다리는 동안 실시간 보기가 다음 프레임에서 장치를 닫는다
    nxt = threading.Thread(target=lambda: next(frames))
    nxt.start()
    assert entered.wait(2)
    assert FakeSource.closed == 1
    release.set()
    t.join(2)
    nxt.join(2)
    assert not nxt.is_alive() and FakeSource.opened == 2
    frames.close()


def test_장치를_열지_못하면_잠시_뒤_다시_연다():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise CameraError(Camera.CAM1, "바쁨")
        return FakeSource()

    frames = live_frames(flaky, CameraGate(), sleep=lambda _: None)
    assert next(frames).startswith(b"\xff\xd8")
    assert calls["n"] == 2
    frames.close()


def test_실시간_보기_경로는_MJPEG를_보낸다(service):
    gate = CameraGate()
    live = {"CAM1": lambda: live_frames(FakeSource, gate, sleep=lambda _: None)}
    server = make_server(App(service, live=live), "127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        with urllib.request.urlopen(base + "/api/live/CAM1", timeout=5) as r:
            assert r.headers.get_content_type() == "multipart/x-mixed-replace"
            chunk = r.read(200)
        assert b"--frame" in chunk and b"image/jpeg" in chunk
        try:
            urllib.request.urlopen(base + "/api/live/CAM9", timeout=5)
            raise AssertionError("404여야 함")
        except urllib.error.HTTPError as e:
            assert e.code == 404
    finally:
        server.shutdown()
        server.server_close()


def test_받지_않는_실시간_보기_연결이_있어도_촬영은_5초를_기다리지_않는다(service, monkeypatch):
    import socket
    import time

    from trace_booth import web

    monkeypatch.setattr(web, "STREAM_WRITE_TIMEOUT", 0.3)

    class BigSource(FakeSource):
        def read(self) -> bytes | None:
            return b"\xff\xd8" + b"x" * 2_000_000 + b"\xff\xd9"  # 소켓 버퍼를 금방 채운다

    gate = CameraGate()
    live = {"CAM1": lambda: live_frames(BigSource, gate, sleep=lambda _: None)}
    server = make_server(App(service, live=live), "127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    stale = socket.socket()
    stale.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
    try:
        stale.connect(("127.0.0.1", server.server_address[1]))
        stale.sendall(b"GET /api/live/CAM1 HTTP/1.1\r\nHost: x\r\n\r\n")  # 요청만 하고 읽지 않는다
        deadline = time.monotonic() + 5
        while gate._live_open == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert gate._live_open == 1
        started = time.monotonic()
        with gate.exclusive(timeout=5.0):
            waited = time.monotonic() - started
        assert waited < 2.0
    finally:
        stale.close()
        server.shutdown()


def test_같은_카메라에_새_연결이_오면_옛_연결은_끝난다():
    _reset()
    alive = {"old": True}
    frames = live_frames(FakeSource, CameraGate(), sleep=lambda _: None, current=lambda: alive["old"])
    next(frames)
    alive["old"] = False
    assert list(frames) == []  # 다음 차례에 끝나고 장치를 닫는다
    assert FakeSource.closed == 1


def test_촬영_중에도_마지막_프레임을_다시_보내_연결을_살려_둔다():
    _reset()
    gate = CameraGate()
    now = {"t": 0.0}
    frames = live_frames(FakeSource, gate, sleep=lambda _: None, clock=lambda: now["t"], keepalive=1.0)
    first = next(frames)
    gate.enter(timeout=0.1)  # 촬영 시작: 실시간 보기는 장치를 닫고 기다린다
    now["t"] = 5.0
    assert next(frames) == first  # 새 장면 대신 마지막 장면을 다시 보낸다
    assert FakeSource.closed == 1
    gate.leave()
    frames.close()


def test_한_장_경로는_JPEG_한_장을_보내고_스트림을_닫는다(service) -> None:
    closed = threading.Event()

    def feed():
        try:
            while True:
                yield b"\xff\xd8one"
        finally:
            closed.set()

    server = make_server(App(service, live={"CAM1": feed}), "127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        with urllib.request.urlopen(base + "/api/live/CAM1/frame", timeout=5) as r:
            assert r.headers.get_content_type() == "image/jpeg"
            assert r.read() == b"\xff\xd8one"
        assert closed.wait(2)  # 한 장 보낸 뒤 장치를 놓는다
        try:
            urllib.request.urlopen(base + "/api/live/CAM9/frame", timeout=5)
            raise AssertionError("404여야 함")
        except urllib.error.HTTPError as e:
            assert e.code == 404
    finally:
        server.shutdown()
