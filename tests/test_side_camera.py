"""11/6: 옆면은 책상 높이에 뒤집어 단 카메라(Brio)로 찍는다. 파일 이름의 카메라 코드는 CAM1 그대로."""

import json
import threading
import urllib.request

import pytest
from fakes import FakeCamera

from trace_booth.camera import check_rotate, rotate_frame
from trace_booth.camera_presets import Presets
from trace_booth.codes import Camera, Light
from trace_booth.session import CaptureService
from trace_booth.shotlist import SHOTLISTS, StepKind
from trace_booth.store import Store
from trace_booth.web import App, make_server


@pytest.fixture
def side_cam() -> FakeCamera:
    return FakeCamera(Camera.CAM1)


@pytest.fixture
def side_presets() -> Presets:
    return Presets({"L1": {"gain": 1}, "L2": {"gain": 2}, "OFF": {"gain": 3}}, "brio")


@pytest.fixture
def svc(tmp_path, cams, presets, side_cam, side_presets) -> CaptureService:
    return CaptureService(
        Store(tmp_path / "booth.db"), cams, presets, tmp_path / "data", steps=SHOTLISTS["mvp"], min_free_bytes=0,
        training_lists=SHOTLISTS, default_list="mvp",
        list_cameras={"side": {Camera.CAM1: side_cam}}, list_presets={"side": side_presets},
    )


def shoot_first_photo(svc: CaptureService, shotlist: str):
    s = svc.start("MB05", shotlist=shotlist)
    while True:  # SLATE·안내를 지나 첫 사진까지
        step = svc.view(s.id).step
        if step.captures:
            shots = svc.shoot(s.id)
            if step.kind is StepKind.SHOT:
                return shots
        svc.advance(s.id)


def test_옆면_목록의_CAM1_사진은_옆면_카메라와_옆면_프리셋으로_찍고_이름은_CAM1이다(svc, cams, side_cam) -> None:
    shots = shoot_first_photo(svc, "side")
    assert side_cam.count == 2 and cams[Camera.CAM1].count == 0
    assert side_cam.settings_seen[-1] == {"gain": 1}
    assert shots[0].camera == "CAM1" and shots[0].filename.startswith("MB05_SF_L1_CAM1_")
    assert json.loads(shots[0].camera_settings) == {"gain": 1}


def test_다른_목록은_원래_카메라와_프리셋을_그대로_쓴다(svc, cams, side_cam, presets) -> None:
    shoot_first_photo(svc, "kb")
    assert side_cam.count == 0 and cams[Camera.CAM1].count > 0
    assert cams[Camera.CAM1].settings_seen[-1] == presets.for_light(Light.L1)


def test_회전은_0_90_180_270만_받는다() -> None:
    assert check_rotate(180) == 180
    with pytest.raises(ValueError):
        check_rotate(45)


def test_180도_회전은_위아래와_좌우를_모두_뒤집는다() -> None:
    np = pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    frame = np.arange(6, dtype=np.uint8).reshape(2, 3)
    assert rotate_frame(frame, 0) is frame
    assert rotate_frame(frame, 180).tolist() == [[5, 4, 3], [2, 1, 0]]


def test_실시간_카메라_목록에_옆면_카메라가_보인다(service) -> None:
    feed = lambda: iter(())
    server = make_server(App(service, live={"CAM1": feed, "CAM2": feed, "SIDE": feed}), "127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{server.server_address[1]}/api/live") as r:
            assert json.loads(r.read()) == {"cameras": ["CAM1", "CAM2", "SIDE"]}
    finally:
        server.shutdown()
        server.server_close()
