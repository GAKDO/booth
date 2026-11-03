"""빠른 검사 세트·탐지를 capture-server에 올리고, 실패하면 로컬 큐에서 다시 시도한다(contracts 1.1.0).

1. 세트: `profile: QUICK`로 만들고 12장을 올린 뒤 완료(checkout.submit_session). 올린 세트는 다시 만들지 않는다.
2. 탐지: 세트가 올라가고 탐지가 끝나면 `analyzedPhotos`(분석한 사진 전부)와 함께 올린다. 다시 탐지하면 다시 보내고,
   서버는 그 세트의 부스 탐지를 통째로 바꾼다. 등급이 매겨진 세트(409 CAPTURE_SET_CLOSED)는 더 보내지 않는다.

사진·탐지 결과는 로컬에 남으므로 몇 번 실패해도 잃지 않는다.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable

from .capture_client import ApiError, CaptureClient, HttpCall, http_call
from .checkout import submit_session
from .store import Store

PROFILE = "QUICK"
RETRY_SECONDS = 30.0


def upload_quick(
    store: Store, session_id: int, client: CaptureClient, now: Callable[[], str],
    http: HttpCall = http_call, sleep: Callable[[float], None] = time.sleep,
) -> None:
    """할 수 있는 단계까지 올린다. 실패는 기록에 남기고 예외를 올리지 않는다(재시도 큐가 다시 부른다)."""
    record = submit_session(store, session_id, client, now, http, sleep, profile=PROFILE)
    if record.status != "SUBMITTED" or record.capture_set_id is None:
        return
    result = store.get_quick_result(session_id)
    sent = store.get_quick_upload(session_id)
    if result is None or result.status != "DONE" or result.result_json is None:
        return
    if sent is not None and (sent.status == "CLOSED" or sent.sent_result_at == result.updated_at):
        return
    data = json.loads(result.result_json)
    if data.get("testOnly"):
        return  # 시험용 결과는 서버 등급에 쓰이지 않게 보내지 않는다(세트 사진은 올라감)
    detections = [
        {"face": p["face"], "light": p["light"], "camera": p["camera"], "class": d["class"],
         "polygon": d["polygon"], "confidence": d["confidence"]}
        for p in data["photos"] for d in p["detections"]
    ]
    analyzed = [{"face": p["face"], "light": p["light"], "camera": p["camera"]} for p in data["photos"]]
    try:
        client.save_detections(record.capture_set_id, data["modelVersion"], detections, analyzed)
    except ApiError as e:
        status = "CLOSED" if e.code == "CAPTURE_SET_CLOSED" else "FAILED"
        store.mark_quick_upload(session_id, status, now(), error=str(e)[:300])
    except OSError as e:
        store.mark_quick_upload(session_id, "FAILED", now(), error=f"{type(e).__name__}: {e}"[:300])
    else:
        store.mark_quick_upload(session_id, "SENT", now(), sent_result_at=result.updated_at)


def upload_json(store: Store, session_id: int) -> dict:
    """결과 API의 upload: 세트(PENDING·SUBMITTED·FAILED)와 탐지(PENDING·SENT·FAILED·CLOSED·TEST_ONLY)."""
    c = store.get_checkout(session_id)
    u = store.get_quick_upload(session_id)
    r = store.get_quick_result(session_id)
    if u is None and r is not None and r.result_json and json.loads(r.result_json).get("testOnly"):
        return {"set": c.status if c else None, "captureSetId": c.capture_set_id if c else None,
                "detections": "TEST_ONLY", "lastError": c.last_error if c else None}
    return {
        "set": c.status if c else None, "captureSetId": c.capture_set_id if c else None,
        "detections": u.status if u else "PENDING",
        "lastError": (u.last_error if u and u.status != "SENT" else None) or (c.last_error if c else None),
    }


class QuickRetryWorker(threading.Thread):
    """올리지 못한 빠른 검사를 RETRY_SECONDS마다 다시 올린다."""

    def __init__(self, store: Store, upload: Callable[[int], None], interval: float = RETRY_SECONDS) -> None:
        super().__init__(daemon=True, name="quick-retry")
        self.store = store
        self.upload = upload
        self.interval = interval
        self._lock = threading.Lock()  # 탐지 직후 올리기와 재시도가 같은 세션을 동시에 올리지 않게

    def run_once(self) -> int:
        ids = self.store.quick_sessions_to_upload()
        for session_id in ids:
            self.upload_now(session_id)
        return len(ids)

    def upload_now(self, session_id: int) -> None:
        with self._lock:
            self.upload(session_id)

    def run(self) -> None:
        while True:
            time.sleep(self.interval)
            try:
                self.run_once()
            except Exception as e:  # noqa: BLE001 — 큐 스레드는 죽지 않고 다음 차례에 다시
                print(f"빠른 검사 재시도 실패(다음 차례에 다시): {type(e).__name__}")
