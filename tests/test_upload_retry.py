"""스토리 2: 네트워크가 끊겨도 사진을 잃지 않기."""

import hashlib

from conftest import run_to_end
from fakes import FakeObjectStore

from trace_booth.camera_presets import Presets
from trace_booth.session import CaptureService
from trace_booth.store import Store
from trace_booth.uploader import UploadWorker, object_key


def drain(worker: UploadWorker) -> None:
    while worker.store.pending_uploads():
        _, failed = worker.run_once()
        assert not failed


def test_저장소에_닿지_않을_때_찍으면_촬영은_실패하지_않고_대기열에_남으며_올리지_못한_장수가_보인다(service):
    objects = FakeObjectStore()
    objects.online = False
    worker = UploadWorker(service.store, objects)
    sid = service.start("MB01").id
    service.shoot(sid)
    service.shoot(sid)
    assert worker.run_once() == (0, 1)
    counts = service.store.upload_counts()
    assert counts["pending"] + counts["failed"] == 3
    assert objects.objects == {}


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


def test_도구를_다시_켜면_이전에_올리지_못한_사진을_계속_올린다(tmp_path, cams, presets):
    db = tmp_path / "booth.db"
    first = CaptureService(Store(db), cams, presets, tmp_path / "data", min_free_bytes=0)
    sid = first.start("MB01").id
    first.shoot(sid)
    first.shoot(sid)
    objects = FakeObjectStore()
    drain(UploadWorker(Store(db), objects))
    assert sorted(objects.objects) == ["MB01/MB01_TOP_L1_CAM1_01.jpg", "MB01/MB01_TOP_L1_CAM2_01.jpg",
                                       "MB01/slate/MB01_SLATE_CAM1_01.jpg"]


def test_같은_사진을_두_번_올려도_같은_이름_해시_파일_하나만_남는다(service):
    objects = FakeObjectStore()
    worker = UploadWorker(service.store, objects)
    sid = service.start("MB01").id
    shot = service.shoot(sid)[0]
    assert worker.upload(shot) == "UPLOADED"
    assert worker.upload(shot) == "UPLOADED"
    assert objects.puts == 1 and len(objects.objects) == 1


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


def test_올리는_객체에_면_조명_카메라_해시_메타데이터를_붙인다(service):
    objects = FakeObjectStore()
    sid = service.start("MB01").id
    service.shoot(sid)
    shot = service.shoot(sid)[1]
    UploadWorker(service.store, objects).upload(shot)
    meta = objects.objects["MB01/MB01_TOP_L1_CAM2_01.jpg"][1]
    assert meta["face"] == "TOP" and meta["light"] == "L1" and meta["camera"] == "CAM2"
    assert meta["sha256"] == shot.sha256


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


def test_프리셋이_없어도_업로드에는_영향이_없다(tmp_path, cams):
    svc = CaptureService(Store(tmp_path / "b.db"), cams, Presets(), tmp_path / "d", min_free_bytes=0)
    sid = svc.start("MB01").id
    svc.shoot(sid)
    objects = FakeObjectStore()
    drain(UploadWorker(svc.store, objects))
    assert len(objects.objects) == 1
