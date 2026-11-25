"""이 파일이 확인하는 것: 부스 출고·반납 모드(BO1 스토리 3). 렌탈의 시점(출고·반납)에 맞춰 촬영 세션을 열고,
완료하면 capture-server가 준 사전 서명 URL로 사진을 올린다. 학습용 저장소(trace-raw)와는 완전히 분리된다.

BO1 스토리 3(출고·반납 모드) 수용 시나리오: 세션 열기, 완료 때 올리기, trace-raw와 분리, 운영 API.
"""

from __future__ import annotations

import json
import threading

import pytest
from conftest import run_to_end
from fake_capture import BASE, SECRET, FakeCapture
from fakes import FakeObjectStore
from test_web import call

from trace_booth.capture_client import BoothAuth, CaptureClient
from trace_booth.checkout import submit_session
from trace_booth.session import CaptureError, InvalidInput
from trace_booth.uploader import UploadWorker
from trace_booth.web import App, make_server

RENTAL = "Ws0HLv6QRbDILPWOcz5I2"
CHECKPOINT = "IPv6mcQOlTkKj6mOecAQt"
NOW = "2026-11-06T10:00:00+09:00"
# 렌탈·시점 ID 길이. 팀 ID 규칙(publicId = NanoID 21자)이라 이 길이가 아니면 출고 모드를 시작하지 않는다.
CHECKPOINT_ID_LEN = 21


@pytest.fixture
def server() -> FakeCapture:
    """가짜 auth-server·capture-server·MinIO(fake_capture.py)."""
    return FakeCapture()


@pytest.fixture
def submitter(service, server):
    """끝난 촬영 세션 ID를 받아 서버로 올리는 함수. 부스 로그인(booth-out)·시각·재시도 대기를 가짜로 고정한다."""
    client = CaptureClient(BASE, BoothAuth(BASE, "booth-out", SECRET, http=server), http=server)
    return lambda sid: submit_session(service.store, sid, client, lambda: NOW, http=server, sleep=lambda _: None)


# 테스트 도구: 출고(OUT) 모드로 촬영 목록 끝까지 찍고 완료한다(켜진 화면은 사유와 함께 건너뜀).
def _finish_out(service, device="MB01"):
    s = service.start(device, "OUT", RENTAL, CHECKPOINT)
    run_to_end(service, s.id, skip_scr1="화면이 켜지지 않음")
    service.complete(s.id)
    return s


# [상황] 출고 모드로 촬영을 끝내고 올린다.
# [기대] 서버의 촬영 세트로 28장이 올라가고 건너뛴 사유도 전달된다. 학습용 저장소에는 하나도 올라가지 않는다
#        (고객 기기 사진이 학습 데이터에 섞이지 않게).
def test_출고_모드에서_찍으면_서버가_준_URL로_올라가고_학습용_저장소에는_올라가지_않는다(service, server, submitter):
    s = _finish_out(service)

    record = submitter(s.id)

    assert record.status == "SUBMITTED" and record.capture_set_id in server.sets
    assert server.sets[record.capture_set_id]["skipped"] == {"SCR1": "화면이 켜지지 않음"}
    assert len(server.objects) == 28
    raw = FakeObjectStore()
    assert UploadWorker(service.store, raw).run_once() == (0, 0)
    assert raw.objects == {}
    assert service.store.upload_counts() == {"pending": 0, "failed": 0, "conflict": 0}


# [상황] 렌탈·시점 ID 없이, 또는 21자가 아닌 ID로 출고 모드를 시작한다.
# [기대] 잘못된 입력으로 시작하지 않는다.
def test_출고_모드는_렌탈_시점_ID가_없거나_21자가_아니면_시작하지_않는다(service):
    with pytest.raises(InvalidInput):
        service.start("MB01", "OUT")
    with pytest.raises(InvalidInput):
        service.start("MB01", "RET", RENTAL, "short")


# [상황] 같은 기기로 다른 렌탈·시점의 촬영을 시작한다.
# [기대] 같은 시점이면 하던 세션을 이어 가고, 다른 시점이면 "다른 렌탈·시점" 오류로 막는다.
def test_같은_기기에_다른_시점의_촬영이_진행_중이면_막고_같은_시점이면_이어_간다(service):
    first = service.start("MB01", "OUT", RENTAL, CHECKPOINT)

    assert service.start("MB01", "OUT", RENTAL, CHECKPOINT).id == first.id
    with pytest.raises(CaptureError, match="다른 렌탈·시점"):
        service.start("MB01", "OUT", RENTAL, "A" * CHECKPOINT_ID_LEN)


# [상황] 올리다가 실패하고, 다시 올린다.
# [기대] 실패(FAILED)와 사유가 남고, 다시 올리면 SUBMITTED(시도 2번). 이미 올린 세션은 또 올리지 않는다.
def test_올리기에_실패하면_사유가_남고_다시_올리면_완료된다(service, server, submitter):
    s = _finish_out(service)
    server.fail_puts = 10_000

    failed = submitter(s.id)
    assert failed.status == "FAILED" and "올리기 실패" in (failed.last_error or "")

    server.fail_puts = 0
    done = submitter(s.id)
    assert done.status == "SUBMITTED" and done.attempts == 2
    assert submitter(s.id).attempts == 2  # 이미 올린 세션은 다시 올리지 않음


@pytest.fixture
def web(service, submitter):
    def start(with_server: bool):
        app = App(service, submitter if with_server else None)
        httpd = make_server(app, "127.0.0.1", 0)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        started.append(httpd)
        return app, f"http://127.0.0.1:{httpd.server_address[1]}"

    started: list = []
    yield start
    for httpd in started:
        httpd.shutdown()
        httpd.server_close()


# [상황] 서버 주소 설정 없이 화면에서 출고 모드를 시작한다.
# [기대] 400과 함께 TRACE_API_URL 설정이 필요하다고 알린다.
#        ※ 응답 코드: 400 = 요청 내용이 형식에 맞지 않음
def test_서버_연결_설정이_없으면_출고_모드_시작은_400(web):
    _, base = web(with_server=False)

    status, _, body = call(base, "POST", "/api/sessions",
                           {"deviceId": "MB01", "mode": "OUT", "rentalId": RENTAL, "checkpointId": CHECKPOINT})
    assert status == 400 and "TRACE_API_URL" in json.loads(body)["detail"]


# [상황] 운영 화면에서 출고 촬영을 시작해 끝까지 찍고 완료한다.
# [기대] 처음엔 PENDING, 올라가면 SUBMITTED와 서버 세트 ID가 화면 상태에 보인다.
def test_운영_화면에서_출고_촬영을_완료하면_세트가_올라가고_상태에_보인다(web, service, server):
    app, base = web(with_server=True)
    status, _, body = call(base, "POST", "/api/sessions",
                           {"deviceId": "MB01", "mode": "OUT", "rentalId": RENTAL, "checkpointId": CHECKPOINT})
    assert status == 201 and json.loads(body)["checkout"]["status"] == "PENDING"
    run_to_end(service, app.current, skip_scr1="화면이 켜지지 않음")

    status, _, body = call(base, "POST", "/api/sessions/current/complete")

    checkout = json.loads(body)["state"]["checkout"]
    assert status == 200 and checkout["status"] == "SUBMITTED"
    assert checkout["captureSetId"] in server.sets
