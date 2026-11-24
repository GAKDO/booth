"""이 파일이 확인하는 것: 빠른 검사(QUICK) 올리기(contracts 1.1.0). 부스가 12장을 찍어 Jetson에서 흠집을 찾은 뒤,
사진 세트와 탐지 결과를 capture-server로 보낸다. 서버가 안 되면 부스에 남겨 두고 나중에 다시 보낸다.

빠른 검사 2부(contracts 1.1.0): QUICK 세트·탐지 올리기와 로컬 재시도 큐.
"""

from __future__ import annotations

import itertools

import pytest
from fake_capture import BASE, SECRET, FakeCapture
from test_detect import SquareDetector
from test_quick_mode import INFO, _inspector, _shoot_quick

from trace_booth.capture_client import BoothAuth, CaptureClient
from trace_booth.detect.engine import DetectError
from trace_booth.quick_upload import QuickRetryWorker, booth_total_ms, upload_json, upload_quick


@pytest.fixture
def server() -> FakeCapture:
    return FakeCapture()


@pytest.fixture
def upload(service, server):
    client = CaptureClient(BASE, BoothAuth(BASE, "booth-1", SECRET, http=server), http=server)
    clock = (f"2026-11-05T10:00:{i:02d}+09:00" for i in itertools.count())
    return lambda sid: upload_quick(service.store, sid, client, lambda: next(clock), http=server,
                                    sleep=lambda _: None)


# 테스트 도구: 빠른 검사 촬영을 끝내고 바닥면에 흠집 하나가 잡힌 결과를 만든다.
def _detected(service, source=None):
    done = _shoot_quick(service)
    inspector = _inspector(service, source=source)
    inspector.start(done.id)
    return done, inspector


# [상황] 빠른 검사 탐지가 끝났다.
# [기대] QUICK 세트로 12장이 올라가고, 탐지 결과(바닥 흠집 1개)와 "분석한 사진 12장 전부" 목록을 함께 보낸다.
#        보낸 뒤 재시도 대기열은 비어 있다.
def test_탐지가_끝나면_QUICK_세트로_12장을_올리고_탐지를_분석한_사진_전부와_함께_보낸다(service, server, upload):
    done, _ = _detected(service)

    upload(done.id)

    (set_id, s), = server.sets.items()
    assert s["request"]["profile"] == "QUICK" and s["status"] == "UPLOADED" and len(server.objects) == 12
    assert len(s["analyzed"]) == 12 and len(server.detections[set_id]) == 1
    assert server.detections[set_id][0]["face"] == "BOT"
    assert upload_json(service.store, done.id)["detections"] == "SENT"
    assert service.store.quick_sessions_to_upload() == []


# [상황] 촬영 시작 91초 뒤 판정이 났다.
# [기대] 탐지에 걸린 시간과 부스 전체 시간(91,000ms)을 함께 보낸다(관리자 성능 그래프용).
def test_탐지_시간을_밀리초로_함께_보내_성능_그래프에_쓰이게_한다(service, server, upload):
    done, _ = _detected(service)
    # 시험 탐지기는 판정 시각이 NOW(10:00:00) 고정이라, 촬영 시작을 91초 전으로 맞춘다
    service.store._conn.execute("UPDATE session SET started_at = ? WHERE id = ?", ("2026-11-05T09:58:29+09:00", done.id))

    upload(done.id)

    (s,) = server.sets.values()
    assert isinstance(s["inferenceMs"], int) and s["inferenceMs"] >= 0
    assert s["boothTotalMs"] == 91_000


# [상황] 시작·판정 시각을 여러 형태(다른 시간대 포함)로 준다.
# [기대] 시간대를 맞춰 ms로 계산하고, 판정이 시작보다 앞서거나 시각을 못 읽으면 보내지 않는다.
def test_부스_전체_시간은_촬영_시작부터_판정까지_ms이고_못_읽거나_거꾸로면_보내지_않는다():
    assert booth_total_ms("2026-11-09T14:02:10+09:00", "2026-11-09T14:03:41+09:00") == 91_000
    assert booth_total_ms("2026-11-09T14:02:10+09:00", "2026-11-09T05:02:20+00:00") == 10_000
    assert booth_total_ms("2026-11-09T14:00:00+09:00", "2026-11-09T16:00:00+09:00") == 3_600_000
    assert booth_total_ms("2026-11-09T14:03:41+09:00", "2026-11-09T14:02:10+09:00") is None
    assert booth_total_ms("시각 아님", "2026-11-09T14:02:10+09:00") is None
    assert booth_total_ms("2026-11-09T14:02:10", "2026-11-09T14:03:41+09:00") is None


# [상황] 서버가 꺼진 상태에서 올리고, 서버가 다시 켜진다.
# [기대] 실패로 부스 대기열에 남았다가 재시도가 올린다. 처음 만든 세트를 이어 써서 세트는 하나뿐이다.
def test_서버가_안_되면_로컬_큐에_남고_다시_되면_재시도가_올린다(service, server, upload):
    done, _ = _detected(service)
    server.fail_puts = 100
    upload(done.id)
    assert upload_json(service.store, done.id)["set"] == "FAILED"
    assert service.store.quick_sessions_to_upload() == [done.id]

    server.fail_puts = 0
    assert QuickRetryWorker(service.store, upload).run_once() == 1

    assert upload_json(service.store, done.id)["detections"] == "SENT"
    assert service.store.quick_sessions_to_upload() == []
    assert len(server.sets) == 1 and len(server.objects) == 12  # 처음 만든 세트를 이어 씀


# [상황] 탐지를 다시 돌렸는데 아직 서버로 보내지 않았다.
# [기대] 탐지 상태가 "보냄"이 아니라 "대기"로 보인다(옛 결과를 보낸 것으로 착각하지 않게).
def test_다시_탐지한_결과를_보내기_전에는_지난번_SENT를_보이지_않는다(service, server, upload):
    done, _inspector = _detected(service)
    upload(done.id)
    service.store.put_quick_result(done.id, "DONE", "2026-11-05T12:00:00+09:00", "CLEAN",
                                   service.store.get_quick_result(done.id).result_json)
    assert upload_json(service.store, done.id)["detections"] == "PENDING"


# [상황] 탐지가 실패했다가 다시 성공한다.
# [기대] 사진 세트는 먼저 올리고, 다시 탐지하면 탐지 결과만 추가로 보낸다(세트는 하나).
def test_탐지에_실패해도_세트는_올리고_다시_탐지하면_탐지만_보낸다(service, server, upload):
    tries = []

    def flaky():
        tries.append(1)
        if len(tries) == 1:
            raise DetectError("엔진 없음")
        return SquareDetector(), INFO

    done, inspector = _detected(service, source=flaky)
    upload(done.id)
    assert len(server.sets) == 1 and server.detections == {}
    assert service.store.quick_sessions_to_upload() == []

    inspector.start(done.id)
    assert service.store.quick_sessions_to_upload() == [done.id]
    upload(done.id)
    assert len(server.sets) == 1 and len(server.detections) == 1


# [상황] 관리자가 이미 등급을 매긴 세트에 탐지를 다시 보내려 한다.
# [기대] 서버가 닫힌 세트로 거절하고, 부스는 CLOSED로 표시하고 더 보내지 않는다.
def test_등급이_매겨진_세트에는_탐지를_더_보내지_않는다(service, server, upload):
    done, _ = _detected(service)
    upload(done.id)
    set_id = next(iter(server.sets))
    server.sets[set_id]["graded"] = True
    service.store.put_quick_result(done.id, "DONE", "2026-11-05T11:00:00+09:00", "CLEAN",
                                   service.store.get_quick_result(done.id).result_json)

    upload(done.id)

    assert upload_json(service.store, done.id)["detections"] == "CLOSED"
    assert service.store.quick_sessions_to_upload() == []
    assert len(server.detections[set_id]) == 1


# [상황] 진짜 모델 없이 시험용 탐지기로 낸 결과.
# [기대] 사진 세트만 올리고 탐지는 TEST_ONLY로 남겨 보내지 않는다(가짜 흠집이 기록에 들어가지 않게).
def test_엔진_없는_시험_결과는_세트만_올리고_탐지는_보내지_않는다(service, server, upload):
    from trace_booth.quick import no_engine_source

    done, _ = _detected(service, source=no_engine_source)
    upload(done.id)

    assert len(server.sets) == 1 and server.detections == {}
    assert upload_json(service.store, done.id)["detections"] == "TEST_ONLY"
    assert service.store.quick_sessions_to_upload() == []
