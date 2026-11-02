"""BO1 스토리 3(출고·반납) 올리기 수용 시나리오: 가짜 auth·capture-server·MinIO로 확인한다."""

from __future__ import annotations

import json

import pytest
from conftest import run_to_end
from fake_capture import BASE, SECRET, FakeCapture

from trace_booth.capture_client import ApiError, BoothAuth, CaptureClient
from trace_booth.checkout import UploadFailed, select_photos, submit
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
    service.shoot(s.id)  # TOP·L1 CAM1·CAM2
    service.retake(s.id)  # TOP·L1을 새 번호로
    photos = select_photos(service.store.shots(s.id))

    assert [(p.face, p.light, p.camera) for p in photos] == [("TOP", "L1", "CAM1"), ("TOP", "L1", "CAM2")]
    assert all(p.path.name.endswith("_02.jpg") for p in photos)


def test_사전_서명_URL이_만료되면_새_세트로_다시_올려_누락이_0건이다(shots, client, server):
    server.expire_urls_once = True

    result = _submit(shots, client, server)

    assert result.new_sets == 1
    assert len(server.sets) == 2
    assert server.sets[result.capture_set_id]["status"] == "UPLOADED"
    assert sum(k.startswith(result.capture_set_id) for k in server.objects) == 28


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
