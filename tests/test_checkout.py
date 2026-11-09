"""BO1 스토리 3(출고·반납) 올리기 수용 시나리오: 가짜 auth·capture-server·MinIO로 확인한다."""

from __future__ import annotations

import json

import pytest
from conftest import run_to_end
from fake_capture import BASE, SECRET, FakeCapture

from trace_booth.capture_client import ApiError, BoothAuth, CaptureClient
from trace_booth.checkout import UPLOAD_WORKERS, UploadFailed, select_photos, submit, upload_workers
from trace_booth.codes import FACE_REQUIRED_BOOTH

RENTAL = "Ws0HLv6QRbDILPWOcz5I2"
CHECKPOINT = "IPv6mcQOlTkKj6mOecAQt"


@pytest.fixture
def server() -> FakeCapture:
    return FakeCapture()


@pytest.fixture
def client(server: FakeCapture) -> CaptureClient:
    return CaptureClient(BASE, BoothAuth(BASE, "booth-out", SECRET, http=server), http=server)


@pytest.fixture
def shots(service):
    s = service.start("MB01")
    run_to_end(service, s.id, skip_scr1="화면이 켜지지 않음")
    return service.store.shots(s.id)


def _submit(shots, client, server, **kw):
    kw.setdefault("workers", 1)  # 만료·다시 받기 시험은 올리는 차례가 정해져 있어야 한다(동시 올리기는 따로 시험)
    return submit(shots, RENTAL, CHECKPOINT, {"SCR1": "화면이 켜지지 않음"}, client, http=server,
                  sleep=lambda _: None, **kw)


def test_출고_세트를_올리면_면_조명_카메라마다_한_장씩_서버_URL로_올라가고_완료된다(shots, client, server):
    result = _submit(shots, client, server)

    assert result.status == "UPLOADED"
    assert result.photos == 28
    assert len(server.objects) == 28
    s = server.sets[result.capture_set_id]
    assert s["request"]["source"] == "BOOTH"
    assert s["request"]["rentalId"] == RENTAL and s["request"]["checkpointId"] == CHECKPOINT
    assert s["skipped"] == {"SCR1": "화면이 켜지지 않음"}
    assert {p["face"] for p in s["request"]["photos"]} == {f.value for f in FACE_REQUIRED_BOOTH}


def test_다시_찍은_사진이_있으면_마지막_장만_올린다(service):
    s = service.start("MB02")
    service.shoot(s.id)  # SLATE
    service.advance(s.id)
    service.shoot(s.id)  # TOP·L1 CAM1·CAM2
    service.retake(s.id)  # TOP·L1을 새 번호로
    photos = select_photos(service.store.shots(s.id))

    assert [(p.face, p.light, p.camera) for p in photos] == [("TOP", "L1", "CAM1"), ("TOP", "L1", "CAM2")]
    assert all(p.path.name.endswith("_02.jpg") for p in photos)


def test_사전_서명_URL이_만료되면_같은_세트에서_남은_사진만_URL을_다시_받아_누락이_0건이다(shots, client, server):
    server.url_budget = [10]  # 처음 받은 URL은 10장 올린 뒤 만료

    result = _submit(shots, client, server)

    assert result.reissued == 1
    assert len(server.sets) == 1
    assert server.sets[result.capture_set_id]["status"] == "UPLOADED"
    assert sum(k.startswith(result.capture_set_id) for k in server.objects) == 28
    assert server.put_ok == {(result.capture_set_id, 0): 10, (result.capture_set_id, 1): 18}


def test_URL을_다시_받아도_계속_만료되면_실패를_알리고_세트를_완료하지_않는다(shots, client, server):
    server.url_budget = [5, 0, 0]

    with pytest.raises(UploadFailed, match="2번 다시 받아도"):
        _submit(shots, client, server)
    assert len(server.sets) == 1
    assert all(s["status"] == "UPLOADING" for s in server.sets.values())


def test_URL을_다시_받을_때_서버가_이미_받은_사진은_다시_올리지_않는다(shots, client, server):
    server.url_budget = [3]
    original = server._reissue

    def already_have_one_more(set_id, data):
        # 만료 직전에 올라간 4번째 사진을 서버는 이미 받았다고 본다
        server.objects[f"{set_id}/3"] = select_photos(shots)[3].path.read_bytes()
        return original(set_id, data)

    server._reissue = already_have_one_more
    result = _submit(shots, client, server)

    assert result.status == "UPLOADED"
    assert server.put_ok[(result.capture_set_id, 1)] == 24


def test_올리는_중_연결이_잠깐_끊기면_다시_시도해_완료된다(shots, client, server):
    server.fail_puts = 2

    assert _submit(shots, client, server).status == "UPLOADED"


def test_연결이_계속_끊기면_실패를_알리고_세트를_완료하지_않는다(shots, client, server):
    server.fail_puts = 10_000

    with pytest.raises(UploadFailed, match="올리기 실패"):
        _submit(shots, client, server)
    assert all(s["status"] == "UPLOADING" for s in server.sets.values())


def test_부스_토큰은_한_번_받아_다시_쓰고_401이면_한_번만_새로_받는다(shots, client, server):
    _submit(shots, client, server)
    assert server.token_count == 1

    server.reject_token = True
    _submit(shots, client, server)
    assert server.token_count == 2


def test_부스_자격이_틀리면_코드와_함께_실패한다(server):
    auth = BoothAuth(BASE, "booth-out", "wrong", http=server)

    with pytest.raises(ApiError) as e:
        auth.token()
    assert (e.value.status, e.value.code) == (401, "CLIENT_CREDENTIALS_INVALID")
    assert "wrong" not in str(e.value)


def test_필수_면을_건너뛰면_서버가_거절하고_실패로_알린다(shots, client, server):
    with pytest.raises(UploadFailed, match="VALIDATION_FAILED"):
        submit(shots, RENTAL, CHECKPOINT, {"KB": "x"}, client, http=server, sleep=lambda _: None)


def test_사전_서명_URL은_repr에_나오지_않는다(shots, client, server):
    _, uploads = client.create_set(RENTAL, CHECKPOINT, select_photos(shots)[:1])

    assert "X-Amz-Signature" not in repr(uploads[0])


def test_서버가_준_URL_순서가_사진과_다르면_올리지_않는다(shots, client, server):
    original = server._create

    def swapped(data):
        resp = original(data)
        body = json.loads(resp.body)
        body["uploads"].reverse()
        resp.body = json.dumps(body).encode()
        return resp

    server._create = swapped
    with pytest.raises(ApiError, match="순서"):
        _submit(shots, client, server)
    assert server.objects == {}


def test_사진을_여러_장_동시에_올려도_28장이_모두_올라가고_완료된다(shots, client, server):
    server.put_delay = 0.02

    result = _submit(shots, client, server, workers=4)

    assert result.status == "UPLOADED"
    assert len(server.objects) == 28
    assert server.max_parallel_puts > 1
    assert result.put_bytes == sum(p.path.stat().st_size for p in select_photos(shots))


def test_동시에_올리다_URL이_만료되면_남은_사진만_다시_받아_누락이_0건이다(shots, client, server):
    server.url_budget = [10]

    result = _submit(shots, client, server, workers=4)

    assert result.reissued == 1
    assert server.sets[result.capture_set_id]["status"] == "UPLOADED"
    assert sum(k.startswith(result.capture_set_id) for k in server.objects) == 28


def test_동시에_올리다_한_장이_계속_끊겨도_나머지는_올려_두고_실패를_알린다(shots, client, server):
    server.fail_puts = 3  # 처음 PUT 세 번 = 첫 사진의 세 번 시도가 모두 끊긴 셈(동시라 어느 사진일지는 모름)

    with pytest.raises(UploadFailed, match="올리기 실패"):
        _submit(shots, client, server, workers=1)
    server.fail_puts = 0
    first = next(iter(server.sets))
    server.fail_puts = 2  # 동시 올리기에서도 잠깐 끊긴 건 다시 시도해 넘어간다
    result = submit(shots, RENTAL, CHECKPOINT, {"SCR1": "화면이 켜지지 않음"}, client, http=server,
                    sleep=lambda _: None, existing_set_id=first, workers=4)
    assert result.status == "UPLOADED"


def test_TRACE_BOOTH_UPLOAD_WORKERS로_동시_장수를_정하고_이상한_값은_기본값(monkeypatch):
    monkeypatch.setenv("TRACE_BOOTH_UPLOAD_WORKERS", "2")
    assert upload_workers() == 2
    monkeypatch.setenv("TRACE_BOOTH_UPLOAD_WORKERS", "99")
    assert upload_workers() == 8
    monkeypatch.setenv("TRACE_BOOTH_UPLOAD_WORKERS", "x")
    assert upload_workers() == UPLOAD_WORKERS
