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
