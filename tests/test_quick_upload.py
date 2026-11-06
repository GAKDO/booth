"""빠른 검사 2부(contracts 1.1.0): QUICK 세트·탐지 올리기와 로컬 재시도 큐."""

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


def _detected(service, source=None):
    done = _shoot_quick(service)
    inspector = _inspector(service, source=source)
    inspector.start(done.id)
    return done, inspector


def test_탐지가_끝나면_QUICK_세트로_12장을_올리고_탐지를_분석한_사진_전부와_함께_보낸다(service, server, upload):
    done, _ = _detected(service)

    upload(done.id)

    (set_id, s), = server.sets.items()
    assert s["request"]["profile"] == "QUICK" and s["status"] == "UPLOADED" and len(server.objects) == 12
    assert len(s["analyzed"]) == 12 and len(server.detections[set_id]) == 1
    assert server.detections[set_id][0]["face"] == "BOT"
    assert upload_json(service.store, done.id)["detections"] == "SENT"
    assert service.store.quick_sessions_to_upload() == []


def test_탐지_시간을_밀리초로_함께_보내_성능_그래프에_쓰이게_한다(service, server, upload):
    done, _ = _detected(service)
    # 시험 탐지기는 판정 시각이 NOW(10:00:00) 고정이라, 촬영 시작을 91초 전으로 맞춘다
    service.store._conn.execute("UPDATE session SET started_at = ? WHERE id = ?", ("2026-11-05T09:58:29+09:00", done.id))

    upload(done.id)

    (s,) = server.sets.values()
    assert isinstance(s["inferenceMs"], int) and s["inferenceMs"] >= 0
    assert s["boothTotalMs"] == 91_000


def test_부스_전체_시간은_촬영_시작부터_판정까지_ms이고_못_읽거나_거꾸로면_보내지_않는다():
    assert booth_total_ms("2026-11-09T14:02:10+09:00", "2026-11-09T14:03:41+09:00") == 91_000
    assert booth_total_ms("2026-11-09T14:02:10+09:00", "2026-11-09T05:02:20+00:00") == 10_000
    assert booth_total_ms("2026-11-09T14:00:00+09:00", "2026-11-09T16:00:00+09:00") == 3_600_000
    assert booth_total_ms("2026-11-09T14:03:41+09:00", "2026-11-09T14:02:10+09:00") is None
    assert booth_total_ms("시각 아님", "2026-11-09T14:02:10+09:00") is None
    assert booth_total_ms("2026-11-09T14:02:10", "2026-11-09T14:03:41+09:00") is None


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


def test_다시_탐지한_결과를_보내기_전에는_지난번_SENT를_보이지_않는다(service, server, upload):
    done, _inspector = _detected(service)
    upload(done.id)
    service.store.put_quick_result(done.id, "DONE", "2026-11-05T12:00:00+09:00", "CLEAN",
                                   service.store.get_quick_result(done.id).result_json)
    assert upload_json(service.store, done.id)["detections"] == "PENDING"


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


def test_엔진_없는_시험_결과는_세트만_올리고_탐지는_보내지_않는다(service, server, upload):
    from trace_booth.quick import no_engine_source

    done, _ = _detected(service, source=no_engine_source)
    upload(done.id)

    assert len(server.sets) == 1 and server.detections == {}
    assert upload_json(service.store, done.id)["detections"] == "TEST_ONLY"
    assert service.store.quick_sessions_to_upload() == []
