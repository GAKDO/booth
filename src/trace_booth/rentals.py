"""빠른 검사의 "검사 기다리는 기기" 목록: rental-server `GET /api/v1/rentals`를 부스 JWT로 읽는다.

빌린 노트북에는 스티커·QR을 붙이지 않으므로(11/3 결정) 부스 태블릿에서 이 목록을 보고 기기를 탭해 고른다.
기다리는 기기 = 진행 중(OPEN) 렌탈 중 지금 시점(currentCheckpoint)이 열려 있는(OPEN, 아직 안 찍은) 것.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from dataclasses import dataclass

from .capture_client import ApiError, BoothAuth, HttpCall, authed_call, http_call

BOOTH_CODE_RE = re.compile(r"^[0-9]{6}$")  # contracts BoothCode
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

    def redeem(self, code: str) -> WaitingDevice:
        """관리자 화면이 발급한 6자리 부스 코드 → 촬영할 기기·렌탈·시점(contracts redeemBoothCode, 한 번만 쓰임)."""
        if not BOOTH_CODE_RE.match(code):
            raise ValueError("부스 코드는 숫자 6자리예요")
        body = json.dumps({"code": code}).encode()
        item = authed_call(self._http, self._auth, "부스 코드", "POST", f"{self._url}/booth-codes/redeem", body,
                           (200,)).json()
        return WaitingDevice(
            str(item["rentalId"]), str(item["deviceId"]), str(item.get("serial") or ""),
            str(item.get("modelCode") or ""), str(item["checkpointId"]), str(item["checkpointType"]),
        )


# 서버 오류 code → 부스 화면 문장
REDEEM_MESSAGES = {
    "BOOTH_CODE_INVALID": "코드가 맞지 않아요. 관리자 화면의 6자리를 다시 확인하세요",
    "BOOTH_CODE_EXPIRED": "만료됐거나 이미 쓴 코드예요. 관리자 화면에서 다시 발급하세요",
    "RATE_LIMITED": "틀린 코드가 너무 많아 잠시 막혔어요. 1분 뒤 다시 넣어 주세요",
    "VALIDATION_FAILED": "부스 코드는 숫자 6자리예요",
}


def redeem_message(e: ApiError) -> str:
    return REDEEM_MESSAGES.get(e.code or "", f"부스 코드를 확인하지 못했어요(HTTP {e.status})")
