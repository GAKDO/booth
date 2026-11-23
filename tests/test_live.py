# 이 파일이 확인하는 것: 부스 화면의 실시간 카메라 보기. 웹캠 하나는 한 번에 한 곳만 열 수 있어서,
# 촬영이 시작되면 실시간 보기가 장치를 내주고 끝나면 다시 연다. 화면에는 MJPEG(연속 JPEG) 스트림으로 보낸다.

"""실시간 카메라 보기: 촬영과 장치 나눠 쓰기, MJPEG 응답."""

import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator

import pytest

from trace_booth import web
from trace_booth.camera import CameraError
from trace_booth.codes import Camera
from trace_booth.live import CameraGate, live_frames
from trace_booth.session import CaptureService
from trace_booth.web import App, make_server


# 테스트 도구: 가짜 실시간 카메라. 프레임을 내고, 몇 번 열고 닫혔는지 센다.
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


# [상황] 실시간 보기로 프레임 3개를 받는다.
# [기대] 서로 다른 JPEG 3장이 오고, 끝나면 장치를 한 번 열고 한 번 닫는다.
def test_실시간_보기는_프레임을_이어서_낸다():
    _reset()
    frames = live_frames(FakeSource, CameraGate(), sleep=lambda _: None)
    got = [next(frames) for _ in range(3)]
    assert got[0].startswith(b"\xff\xd8") and len(set(got)) == 3
    frames.close()
    assert FakeSource.opened == 1 and FakeSource.closed == 1


# [상황] 실시간 보기 중 촬영이 시작되고 끝난다.
# [기대] 촬영이 기다리는 동안 실시간 보기가 장치를 닫고, 촬영이 끝나면 다시 연다.
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


# [상황] 처음엔 장치를 열지 못한다.
# [기대] 잠시 뒤 다시 열어 프레임을 낸다.
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


# [상황] 부스 웹 서버의 실시간 보기 주소를 연다. 없는 카메라 주소도 연다.
# [기대] MJPEG 스트림이 오고, 없는 카메라는 404.
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


# [상황] 요청만 하고 데이터를 읽지 않는(멈춘) 실시간 보기 연결이 있다.
# [기대] 촬영이 그 연결 때문에 오래(2초 이상) 기다리지 않는다.
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


# [상황] 같은 카메라에 새 실시간 보기 연결이 온다.
# [기대] 옛 연결은 다음 차례에 끝나고 장치를 닫는다.
def test_같은_카메라에_새_연결이_오면_옛_연결은_끝난다():
    _reset()
    alive = {"old": True}
    frames = live_frames(FakeSource, CameraGate(), sleep=lambda _: None, current=lambda: alive["old"])
    next(frames)
    alive["old"] = False
    assert list(frames) == []  # 다음 차례에 끝나고 장치를 닫는다
    assert FakeSource.closed == 1


# [상황] 실시간 보기 중 촬영이 시작되어 장치를 내줬다.
# [기대] 새 장면 대신 마지막 장면을 다시 보내 화면 연결이 끊기지 않게 한다.
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


# [상황] 한 장만 받는 주소를 연다.
# [기대] JPEG 한 장을 보내고 장치를 놓는다. 없는 카메라는 404.
def test_한_장_경로는_JPEG_한_장을_보내고_스트림을_닫는다(service: CaptureService) -> None:
    closed = threading.Event()

    def feed() -> Iterator[bytes]:
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


# [상황] 카메라가 프레임을 내지 않는다.
# [기대] 오래 기다리지 않고(2초 안) 503을 돌려준다.
def test_한_장_경로는_첫_프레임이_제때_안_오면_503을_돌려준다(
    service: CaptureService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(web, "FRAME_WAIT", 0.1)
    release = threading.Event()

    def feed() -> Iterator[bytes]:  # 쉬는 카메라: 프레임을 내지 않고 기다린다
        release.wait(5)
        yield b"\xff\xd8late"

    server = make_server(App(service, live={"SIDE": feed}), "127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        started = time.monotonic()
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(base + "/api/live/SIDE/frame", timeout=5)
        assert e.value.code == 503
        assert time.monotonic() - started < 2
    finally:
        release.set()
        server.shutdown()
