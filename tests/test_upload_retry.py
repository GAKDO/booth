"""이 파일이 확인하는 것: 학습 데이터 촬영(BO2) 스토리 2. 네트워크가 끊겨도 사진을 잃지 않기.
찍은 사진은 먼저 부스에 저장하고, 대기열로 MinIO(trace-raw)에 올린다. 실패하면 나중에 자동으로 다시 올린다.

스토리 2: 네트워크가 끊겨도 사진을 잃지 않기.
"""

import hashlib

from conftest import run_to_end
from fakes import FakeObjectStore

from trace_booth.camera_presets import Presets
from trace_booth.session import CaptureService
from trace_booth.store import Store
from trace_booth.uploader import UploadWorker, object_key


# 테스트 도구: 대기열이 빌 때까지 올리기를 반복한다.
def drain(worker: UploadWorker) -> None:
    while worker.store.pending_uploads():
        _, failed = worker.run_once()
        assert not failed


# [상황] 저장소가 꺼진 상태에서 사진 3장을 찍는다.
# [기대] 촬영은 성공하고, 3장이 대기·실패 상태로 남아 화면에 장수가 보인다. 저장소에는 아무것도 없다.
def test_저장소에_닿지_않을_때_찍으면_촬영은_실패하지_않고_대기열에_남으며_올리지_못한_장수가_보인다(service):
    objects = FakeObjectStore()
    objects.online = False
    worker = UploadWorker(service.store, objects)
    sid = service.start("MB01").id
    service.shoot(sid)
    service.advance(sid)
    service.shoot(sid)
    assert worker.run_once() == (0, 1)
    counts = service.store.upload_counts()
    assert counts["pending"] + counts["failed"] == 3
    assert objects.objects == {}


# [상황] 오프라인으로 끝까지 찍은 뒤 저장소가 다시 켜진다.
# [기대] 저절로 29장(필수 28 + 기기 ID 판 1)이 모두 올라가고, 해시가 일치하며 누락 0건.
def test_저장소가_다시_닿으면_아무것도_하지_않아도_모두_올라가고_누락이_0건이다(service):
    objects = FakeObjectStore()
    objects.online = False
    worker = UploadWorker(service.store, objects)
    sid = service.start("MB01").id
    run_to_end(service, sid, skip_scr1="흰 화면 불가")
    worker.run_once()
    objects.online = True
    drain(worker)
    shots = service.store.shots(sid)
    assert len(objects.objects) == len(shots) == 29
    for s in shots:
        data, meta = objects.objects[object_key(s)]
        assert hashlib.sha256(data).hexdigest() == s.sha256 == meta["sha256"]
    assert service.store.upload_counts() == {"pending": 0, "failed": 0, "conflict": 0}


# [상황] 못 올린 사진이 있는 채로 부스 앱을 껐다 켠다.
# [기대] 이전에 못 올린 사진을 이어서 올린다.
def test_도구를_다시_켜면_이전에_올리지_못한_사진을_계속_올린다(tmp_path, cams, presets):
    db = tmp_path / "booth.db"
    first = CaptureService(Store(db), cams, presets, tmp_path / "data", min_free_bytes=0)
    sid = first.start("MB01").id
    first.shoot(sid)
    first.advance(sid)
    first.shoot(sid)
    objects = FakeObjectStore()
    drain(UploadWorker(Store(db), objects))
    assert sorted(objects.objects) == ["MB01/MB01_TOP_L1_CAM1_01.jpg", "MB01/MB01_TOP_L1_CAM2_01.jpg",
                                       "MB01/slate/MB01_SLATE_CAM1_01.jpg"]


# [상황] 같은 사진을 두 번 올린다.
# [기대] 실제 업로드는 한 번만 일어나고 파일 하나만 남는다.
def test_같은_사진을_두_번_올려도_같은_이름_해시_파일_하나만_남는다(service):
    objects = FakeObjectStore()
    worker = UploadWorker(service.store, objects)
    sid = service.start("MB01").id
    shot = service.shoot(sid)[0]
    assert worker.upload(shot) == "UPLOADED"
    assert worker.upload(shot) == "UPLOADED"
    assert objects.puts == 1 and len(objects.objects) == 1


# [상황] 저장소의 같은 이름 자리에 내용이 다른 파일이 이미 있다.
# [기대] 덮어쓰지 않고 충돌(CONFLICT)로 남긴다. 대기열에서는 빠진다(사람이 확인할 일).
def test_같은_키에_다른_해시가_있으면_덮어쓰지_않고_CONFLICT로_남긴다(service):
    objects = FakeObjectStore()
    worker = UploadWorker(service.store, objects)
    sid = service.start("MB01").id
    shot = service.shoot(sid)[0]
    objects.objects[object_key(shot)] = (b"other", {"sha256": "0" * 64})
    assert worker.upload(shot) == "CONFLICT"
    assert objects.objects[object_key(shot)][0] == b"other"
    assert service.store.upload_counts()["conflict"] == 1
    assert service.store.pending_uploads() == []


# [상황] 올리기가 두 번 실패한다.
# [기대] 실패 상태·시도 2번·사유(ConnectionError)를 남기고, 서명 URL 같은 민감한 값은 기록하지 않는다.
def test_실패하면_사유와_시도_횟수를_남기고_서명_URL은_남기지_않는다(service):
    objects = FakeObjectStore()
    objects.online = False
    worker = UploadWorker(service.store, objects)
    sid = service.start("MB01").id
    shot = service.shoot(sid)[0]
    worker.upload(shot)
    worker.upload(shot)
    (saved,) = service.store.pending_uploads()
    assert saved.upload_status == "FAILED" and saved.upload_attempts == 2
    assert "ConnectionError" in saved.last_error


# [상황] 사진 하나를 올린다.
# [기대] 파일에 면·조명·카메라·해시 정보가 함께 붙는다.
def test_올리는_객체에_면_조명_카메라_해시_메타데이터를_붙인다(service):
    objects = FakeObjectStore()
    sid = service.start("MB01").id
    service.shoot(sid)
    service.advance(sid)
    shot = service.shoot(sid)[1]
    UploadWorker(service.store, objects).upload(shot)
    meta = objects.objects["MB01/MB01_TOP_L1_CAM2_01.jpg"][1]
    assert meta["face"] == "TOP" and meta["light"] == "L1" and meta["camera"] == "CAM2"
    assert meta["sha256"] == shot.sha256


# [상황] 올리기가 계속 실패한다.
# [기대] 2·4·8·16·32초로 늘리다가 60초에서 멈춘다(저장소를 두드리지 않게).
def test_실패가_이어지면_대기_시간을_늘리고_최대_60초를_넘지_않는다(service):
    import threading

    objects = FakeObjectStore()
    objects.online = False
    waits: list[float] = []
    stop = threading.Event()

    def sleep(sec: float) -> None:
        waits.append(sec)
        if len(waits) >= 8:
            stop.set()

    sid = service.start("MB01").id
    service.shoot(sid)
    UploadWorker(service.store, objects, sleep=sleep).run_forever(stop)
    assert waits == [2, 4, 8, 16, 32, 60, 60, 60]


# [상황] 카메라 설정 파일 없이 찍고 올린다.
# [기대] 올리기는 정상이다.
def test_프리셋이_없어도_업로드에는_영향이_없다(tmp_path, cams):
    svc = CaptureService(Store(tmp_path / "b.db"), cams, Presets(), tmp_path / "d", min_free_bytes=0)
    sid = svc.start("MB01").id
    svc.shoot(sid)
    objects = FakeObjectStore()
    drain(UploadWorker(svc.store, objects))
    assert len(objects.objects) == 1
