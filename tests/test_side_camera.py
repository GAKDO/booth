# 이 파일이 확인하는 것: 옆면 전용 카메라(11/6). 옆면은 책상 높이에 뒤집어 단 카메라(Brio)로 찍지만,
# 파일 이름의 카메라 코드는 CAM1 그대로 쓴다(이름 규칙과 학습 데이터를 바꾸지 않기 위함).

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


# 테스트 도구: 기기 ID 판·안내 단계를 지나 첫 사진 단계까지 찍는다.
def shoot_first_photo(svc: CaptureService, shotlist: str):
    s = svc.start("MB05", shotlist=shotlist)
    while True:  # SLATE·안내를 지나 첫 사진까지
        step = svc.view(s.id).step
        if step.captures:
            shots = svc.shoot(s.id)
            if step.kind is StepKind.SHOT:
                return shots
        svc.advance(s.id)


# [상황] 옆면 목록으로 첫 사진을 찍는다.
# [기대] 실제로는 옆면 카메라와 옆면용 설정으로 찍히고, 파일 이름·기록에는 CAM1로 남는다.
def test_옆면_목록의_CAM1_사진은_옆면_카메라와_옆면_프리셋으로_찍고_이름은_CAM1이다(svc, cams, side_cam) -> None:
    shots = shoot_first_photo(svc, "side")
    assert side_cam.count == 2 and cams[Camera.CAM1].count == 0
    assert side_cam.settings_seen[-1] == {"gain": 1}
    assert shots[0].camera == "CAM1" and shots[0].filename.startswith("MB05_SF_L1_CAM1_")
    assert json.loads(shots[0].camera_settings) == {"gain": 1}


# [상황] MVP 목록으로 찍는다.
# [기대] 옆면 카메라는 쓰지 않고 원래 CAM1과 조명별 설정을 쓴다.
def test_다른_목록은_원래_카메라와_프리셋을_그대로_쓴다(svc, cams, side_cam, presets) -> None:
    shoot_first_photo(svc, "kb")
    assert side_cam.count == 0 and cams[Camera.CAM1].count > 0
    assert cams[Camera.CAM1].settings_seen[-1] == presets.for_light(Light.L1)


# [상황] 카메라 화면 회전 값을 설정한다.
# [기대] 0·90·180·270만 받고 그 밖은 거절한다.
def test_회전은_0_90_180_270만_받는다() -> None:
    assert check_rotate(180) == 180
    with pytest.raises(ValueError):
        check_rotate(45)


# [상황] 뒤집어 단 카메라 화면을 180도 돌린다.
# [기대] 위아래·좌우가 모두 뒤집힌다(0도면 그대로).
def test_180도_회전은_위아래와_좌우를_모두_뒤집는다() -> None:
    np = pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    frame = np.arange(6, dtype=np.uint8).reshape(2, 3)
    assert rotate_frame(frame, 0) is frame
    assert rotate_frame(frame, 180).tolist() == [[5, 4, 3], [2, 1, 0]]


# [상황] 부스 화면이 실시간으로 볼 수 있는 카메라 목록을 묻는다.
# [기대] CAM1·CAM2·SIDE가 나온다.
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
