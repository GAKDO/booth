"""빠른 검사의 "검사 기다리는 기기" 목록: rental-server `GET /api/v1/rentals`를 부스 JWT로 읽는다.

빌린 노트북에는 스티커·QR을 붙이지 않으므로(11/3 결정) 부스 태블릿에서 이 목록을 보고 기기를 탭해 고른다.
기다리는 기기 = 진행 중(OPEN) 렌탈 중 지금 시점(currentCheckpoint)이 열려 있는(OPEN, 아직 안 찍은) 것.
"""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass

from .capture_client import BoothAuth, HttpCall, authed_call, http_call

PAGE_LIMIT = 100  # contracts common Limit 최대
MAX_PAGES = 5


@dataclass(frozen=True)
class WaitingDevice:
    rental_id: str
    device_id: str  # rental-server 기기 publicId(21자). 부스 사진 파일명의 기기 자리에 쓴다
    serial: str
    model_code: str
    checkpoint_id: str
    checkpoint_type: str  # OUT·RCV·RET

    def to_json(self) -> dict[str, str]:
        return {
            "rentalId": self.rental_id, "deviceId": self.device_id, "serial": self.serial,
            "modelCode": self.model_code, "checkpointId": self.checkpoint_id, "checkpointType": self.checkpoint_type,
        }


class RentalClient:
    def __init__(self, base_url: str, auth: BoothAuth, http: HttpCall = http_call) -> None:
        self._url = base_url.rstrip("/") + "/api/v1/rentals"
        self._auth = auth
        self._http = http

    def waiting(self) -> list[WaitingDevice]:
        """검사 기다리는 기기. 서버 순서(최근 생성 순)를 그대로 둔다."""
        out: list[WaitingDevice] = []
        cursor: str | None = None
        for _ in range(MAX_PAGES):
            query = {"status": "OPEN", "limit": str(PAGE_LIMIT)}
            if cursor:
                query["cursor"] = cursor
            url = f"{self._url}?{urllib.parse.urlencode(query)}"
            page = authed_call(self._http, self._auth, "렌탈 목록", "GET", url, None, (200,)).json()
            for item in page.get("items", []):
                cp = item.get("currentCheckpoint")
                if item.get("status") != "OPEN" or not cp or cp.get("status") != "OPEN":
                    continue
                out.append(WaitingDevice(
                    str(item["rentalId"]), str(item["deviceId"]), str(item.get("serial") or ""),
                    str(item.get("modelCode") or ""), str(cp["checkpointId"]), str(cp["type"]),
                ))
            cursor = page.get("nextCursor")
            if not cursor:
                break
        return out
