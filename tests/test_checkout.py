# 이 파일이 확인하는 것: 부스 출고·반납(BO1 스토리 3) 사진 올리기. 부스가 capture-server에 촬영 세트를 만들고,
# 받은 사전 서명 URL(잠깐만 쓸 수 있는 업로드 주소)로 MinIO에 사진을 올린 뒤 세트를 완료한다.
# URL 만료·연결 끊김·토큰 만료 같은 상황에서도 사진이 빠지지 않는지 본다. 서버들은 가짜(fake_capture.py)다.

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


# 테스트 도구: 촬영한 세션을 서버로 올린다(기본은 한 장씩 차례대로).
def _submit(shots, client, server, **kw):
    kw.setdefault("workers", 1)  # 만료·다시 받기 시험은 올리는 차례가 정해져 있어야 한다(동시 올리기는 따로 시험)
    return submit(shots, RENTAL, CHECKPOINT, {"SCR1": "화면이 켜지지 않음"}, client, http=server,
                  sleep=lambda _: None, **kw)


# [상황] 출고 촬영을 마친 세션을 올린다.
# [기대] 28장이 올라가 세트가 UPLOADED로 완료된다. 요청에 출처(BOOTH)·렌탈·시점, 건너뛴 면 사유, 필수 면이 모두 들어 있다.
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


# [상황] 윗면 L1을 찍고 다시 찍었다.
# [기대] 다시 찍은 마지막 장(_02)만 올릴 대상이 된다.
def test_다시_찍은_사진이_있으면_마지막_장만_올린다(service):
    s = service.start("MB02")
    service.shoot(s.id)  # SLATE
    service.advance(s.id)
    service.shoot(s.id)  # TOP·L1 CAM1·CAM2
    service.retake(s.id)  # TOP·L1을 새 번호로
    photos = select_photos(service.store.shots(s.id))

    assert [(p.face, p.light, p.camera) for p in photos] == [("TOP", "L1", "CAM1"), ("TOP", "L1", "CAM2")]
    assert all(p.path.name.endswith("_02.jpg") for p in photos)


# [상황] 처음 받은 URL이 10장 올린 뒤 만료된다.
# [기대] 같은 세트에서 남은 18장만 URL을 다시 받아 올리고, 28장 모두 올라가 완료된다(세트는 하나).
def test_사전_서명_URL이_만료되면_같은_세트에서_남은_사진만_URL을_다시_받아_누락이_0건이다(shots, client, server):
    server.url_budget = [10]  # 처음 받은 URL은 10장 올린 뒤 만료

    result = _submit(shots, client, server)

    assert result.reissued == 1
    assert len(server.sets) == 1
    assert server.sets[result.capture_set_id]["status"] == "UPLOADED"
    assert sum(k.startswith(result.capture_set_id) for k in server.objects) == 28
    assert server.put_ok == {(result.capture_set_id, 0): 10, (result.capture_set_id, 1): 18}


# [상황] URL을 다시 받아도 계속 만료된다.
# [기대] "2번 다시 받아도" 실패를 알리고 세트는 완료하지 않는다(빠진 채 완료되지 않게).
def test_URL을_다시_받아도_계속_만료되면_실패를_알리고_세트를_완료하지_않는다(shots, client, server):
    server.url_budget = [5, 0, 0]

    with pytest.raises(UploadFailed, match="2번 다시 받아도"):
        _submit(shots, client, server)
    assert len(server.sets) == 1
    assert all(s["status"] == "UPLOADING" for s in server.sets.values())


# [상황] 만료 직전에 올라간 사진을 서버는 이미 받은 것으로 본다.
# [기대] 그 사진은 빼고 나머지 24장만 다시 올린다.
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


# [상황] 처음 몇 번 업로드가 연결 실패한다.
# [기대] 다시 시도해 완료된다.
def test_올리는_중_연결이_잠깐_끊기면_다시_시도해_완료된다(shots, client, server):
    server.fail_puts = 2

    assert _submit(shots, client, server).status == "UPLOADED"


# [상황] 업로드가 계속 실패한다.
# [기대] "올리기 실패"를 알리고 세트는 완료하지 않는다.
def test_연결이_계속_끊기면_실패를_알리고_세트를_완료하지_않는다(shots, client, server):
    server.fail_puts = 10_000

    with pytest.raises(UploadFailed, match="올리기 실패"):
        _submit(shots, client, server)
    assert all(s["status"] == "UPLOADING" for s in server.sets.values())


# [상황] 부스 로그인 토큰으로 여러 요청을 보내다 한 번 401(만료)을 받는다.
# [기대] 토큰은 한 번 받아 재사용하고, 401이면 한 번만 새로 받아 이어 간다.
def test_부스_토큰은_한_번_받아_다시_쓰고_401이면_한_번만_새로_받는다(shots, client, server):
    _submit(shots, client, server)
    assert server.token_count == 1

    server.reject_token = True
    _submit(shots, client, server)
    assert server.token_count == 2


# [상황] 부스 자격(client id/secret)이 틀렸다.
# [기대] 401 CLIENT_CREDENTIALS_INVALID로 실패하고 오류 문구에 비밀 값이 나오지 않는다.
def test_부스_자격이_틀리면_코드와_함께_실패한다(server):
    auth = BoothAuth(BASE, "booth-out", "wrong", http=server)

    with pytest.raises(ApiError) as e:
        auth.token()
    assert (e.value.status, e.value.code) == (401, "CLIENT_CREDENTIALS_INVALID")
    assert "wrong" not in str(e.value)


# [상황] 필수 면을 빼고 올린다.
# [기대] 서버가 VALIDATION_FAILED로 거절하고 실패로 알린다.
def test_필수_면을_건너뛰면_서버가_거절하고_실패로_알린다(shots, client, server):
    with pytest.raises(UploadFailed, match="VALIDATION_FAILED"):
        submit(shots, RENTAL, CHECKPOINT, {"KB": "x"}, client, http=server, sleep=lambda _: None)


# [상황] 업로드 대상 정보를 출력(로그)한다.
# [기대] URL의 서명 값이 보이지 않는다(로그로 업로드 권한이 새지 않게).
def test_사전_서명_URL은_repr에_나오지_않는다(shots, client, server):
    _, uploads = client.create_set(RENTAL, CHECKPOINT, select_photos(shots)[:1])

    assert "X-Amz-Signature" not in repr(uploads[0])


# [상황] 서버가 준 URL 목록 순서가 요청한 사진 순서와 다르다.
# [기대] "순서" 오류로 하나도 올리지 않는다(사진이 엉뚱한 자리에 올라가는 것 방지).
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


# [상황] 여러 장을 동시에 올린다.
# [기대] 실제로 동시에 올라가고, 28장 모두 올라가 완료되며, 올린 바이트 수가 사진 크기 합과 같다.
def test_사진을_여러_장_동시에_올려도_28장이_모두_올라가고_완료된다(shots, client, server):
    server.put_delay = 0.02

    result = _submit(shots, client, server, workers=4)

    assert result.status == "UPLOADED"
    assert len(server.objects) == 28
    assert server.max_parallel_puts > 1
    assert result.put_bytes == sum(p.path.stat().st_size for p in select_photos(shots))


# [상황] 동시에 올리던 중 URL이 만료된다.
# [기대] 남은 사진만 다시 받아 28장 모두 올라가 완료된다.
def test_동시에_올리다_URL이_만료되면_남은_사진만_다시_받아_누락이_0건이다(shots, client, server):
    server.url_budget = [10]

    result = _submit(shots, client, server, workers=4)

    assert result.reissued == 1
    assert server.sets[result.capture_set_id]["status"] == "UPLOADED"
    assert sum(k.startswith(result.capture_set_id) for k in server.objects) == 28


# [상황] 동시에 올리는데 6번째 사진만 계속 실패한다.
# [기대] 나머지 27장은 올려 두고 실패를 알린다. 다시 시도하면 남은 1장만 올려 완료된다.
def test_동시에_올리다_한_장이_계속_끊겨도_나머지는_올려_두고_실패를_알린다(shots, client, server):
    server.always_fail_index = 5

    with pytest.raises(UploadFailed, match="올리기 실패"):
        _submit(shots, client, server, workers=4)
    set_id = next(iter(server.sets))
    assert sorted(int(k.split("/")[1]) for k in server.objects) == [i for i in range(28) if i != 5]
    assert server.sets[set_id]["status"] == "UPLOADING"

    server.always_fail_index = None  # 다시 시도하면 남은 한 장만 올려 완료된다
    result = _submit(shots, client, server, workers=4, existing_set_id=set_id)
    assert result.status == "UPLOADED"
    assert server.put_ok[(set_id, 1)] == 1


# [상황] 동시 업로드 수를 환경 변수로 2·8·이상한 값으로 준다.
# [기대] 준 값을 쓰고, 이상한 값이면 기본값을 쓴다.
def test_TRACE_BOOTH_UPLOAD_WORKERS로_동시_장수를_정하고_이상한_값은_기본값(monkeypatch):
    monkeypatch.setenv("TRACE_BOOTH_UPLOAD_WORKERS", "2")
    assert upload_workers() == 2
    monkeypatch.setenv("TRACE_BOOTH_UPLOAD_WORKERS", "99")
    assert upload_workers() == 8
    monkeypatch.setenv("TRACE_BOOTH_UPLOAD_WORKERS", "x")
    assert upload_workers() == UPLOAD_WORKERS
