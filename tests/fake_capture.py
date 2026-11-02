"""auth-server·capture-server·MinIO를 흉내 내는 가짜 HTTP. contracts C1의 응답 모양만 따른다."""

from __future__ import annotations

import hashlib
import json

from trace_booth.capture_client import Response
from trace_booth.codes import FACE_REQUIRED_BOOTH

BASE = "https://api.test"
SECRET = "test-only-secret"


class FakeCapture:
    def __init__(self) -> None:
        self.sets: dict[str, dict] = {}
        self.objects: dict[str, bytes] = {}
        self.calls: list[tuple[str, str]] = []
        self.token_count = 0
        self.expire_urls_once = False  # 첫 세트의 URL이 만료된 것처럼 403
        self.fail_puts = 0  # 처음 n번 PUT은 연결 실패
        self.reject_token = False  # 다음 API 호출 한 번은 401
        self.completed_keys: dict[str, dict] = {}

    def __call__(self, method: str, url: str, headers: dict[str, str], body: bytes | None) -> Response:
        self.calls.append((method, url))
        if url == f"{BASE}/api/v1/auth/booth/token":
            data = json.loads(body or b"{}")
            if data.get("clientSecret") != SECRET:
                return self._problem(401, "CLIENT_CREDENTIALS_INVALID")
            self.token_count += 1
            return self._json(200, {"accessToken": f"tok{self.token_count}", "tokenType": "Bearer",
                                    "expiresIn": 3600, "boothId": "booth-out"})
        if method == "PUT":
            return self._put(url, headers, body or b"")
        if self.reject_token:
            self.reject_token = False
            return self._problem(401, "UNAUTHORIZED")
        if headers.get("Authorization") != f"Bearer tok{self.token_count}":
            return self._problem(401, "UNAUTHORIZED")
        if url == f"{BASE}/api/v1/captures/sets":
            return self._create(json.loads(body or b"{}"))
        if url.endswith("/complete"):
            return self._complete(url.split("/")[-2], json.loads(body or b"{}"), headers.get("Idempotency-Key", ""))
        return self._problem(404, "NOT_FOUND")

    def _create(self, data: dict) -> Response:
        set_id = f"set{len(self.sets) + 1:018d}"
        expired = self.expire_urls_once and not self.sets
        self.sets[set_id] = {"request": data, "expired": expired, "status": "UPLOADING"}
        uploads = [
            {"face": p["face"], "light": p["light"], "camera": p["camera"], "method": "PUT",
             "url": f"https://minio.test/trace-captures/{set_id}/{i}?X-Amz-Signature=s",
             "headers": {"Content-Type": p["contentType"]}, "expiresAt": "2026-11-06T10:10:00+09:00"}
            for i, p in enumerate(data["photos"])
        ]
        return self._json(201, {"captureSetId": set_id, "uploads": uploads})

    def _put(self, url: str, headers: dict[str, str], body: bytes) -> Response:
        if self.fail_puts:
            self.fail_puts -= 1
            raise ConnectionResetError("가짜 연결 끊김")
        set_id, index = url.split("?")[0].split("/")[-2:]
        if self.sets[set_id]["expired"]:
            return Response(403, b"<Error><Code>AccessDenied</Code></Error>")
        self.objects[f"{set_id}/{index}"] = body
        return Response(200)

    def _complete(self, set_id: str, data: dict, key: str) -> Response:
        if key in self.completed_keys:
            return self._json(202, self.completed_keys[key])
        s = self.sets[set_id]
        photos = s["request"]["photos"]
        skipped = data.get("skippedFaces", {})
        if any(f in {x.value for x in FACE_REQUIRED_BOOTH} for f in skipped):
            return self._problem(400, "VALIDATION_FAILED")
        missing = sorted({x.value for x in FACE_REQUIRED_BOOTH} - {p["face"] for p in photos})
        if missing:
            return self._problem(409, "CAPTURE_FACES_MISSING", missingFaces=missing)
        for i, p in enumerate(photos):
            got = self.objects.get(f"{set_id}/{i}")
            if got is None or hashlib.sha256(got).hexdigest() != p["sha256"]:
                return self._problem(409, "CAPTURE_HASH_MISMATCH")
        s["status"] = "UPLOADED"
        s["skipped"] = skipped
        result = {"captureSetId": set_id, "status": "UPLOADED", "source": "BOOTH"}
        self.completed_keys[key] = result
        return self._json(202, result)

    @staticmethod
    def _json(status: int, data: dict) -> Response:
        return Response(status, json.dumps(data).encode())

    @staticmethod
    def _problem(status: int, code: str, **extra: object) -> Response:
        return Response(status, json.dumps({"status": status, "code": code, **extra}).encode())
