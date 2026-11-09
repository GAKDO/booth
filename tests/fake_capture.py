"""auth-server·capture-server·MinIO를 흉내 내는 가짜 HTTP. contracts C1의 응답 모양만 따른다."""

from __future__ import annotations

import hashlib
import json
import threading
import time

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
        # 세대(0=세트 생성, 1부터=다시 받기)마다 URL로 성공할 수 있는 PUT 수. 넘으면 403(만료). 없으면 무제한.
        self.url_budget: list[int] = []
        self.put_ok: dict[tuple[str, int], int] = {}
        self.detections: dict[str, list[dict]] = {}  # 세트마다 마지막 요청의 부스 탐지
        self.fail_puts = 0  # 처음 n번 PUT은 연결 실패
        self.reject_token = False  # 다음 API 호출 한 번은 401
        self.completed_keys: dict[str, dict] = {}
        self._lock = threading.RLock()  # 부스가 사진을 여러 장 동시에 올리므로 상태를 한 번에 하나씩 바꾼다
        self.max_parallel_puts = 0  # 동시에 들어온 PUT 수의 최댓값
        self._puts_in_flight = 0
        self.put_delay = 0.0  # PUT 하나가 걸리는 시간(동시 올리기 확인용)

    def __call__(self, method: str, url: str, headers: dict[str, str], body: bytes | None) -> Response:
        if method == "PUT" and self.put_delay:
            with self._lock:
                self._puts_in_flight += 1
                self.max_parallel_puts = max(self.max_parallel_puts, self._puts_in_flight)
            time.sleep(self.put_delay)
            with self._lock:
                self._puts_in_flight -= 1
        with self._lock:
            return self._call(method, url, headers, body)

    def _call(self, method: str, url: str, headers: dict[str, str], body: bytes | None) -> Response:
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
        if url.endswith("/uploads"):
            return self._reissue(url.split("/")[-2], json.loads(body or b"{}"))
        if url.endswith("/detections"):
            return self._detections(url.split("/")[-2], json.loads(body or b"{}"))
        if url.endswith("/complete"):
            return self._complete(url.split("/")[-2], json.loads(body or b"{}"), headers.get("Idempotency-Key", ""))
        return self._problem(404, "NOT_FOUND")

    def _create(self, data: dict) -> Response:
        set_id = f"set{len(self.sets) + 1:018d}"
        self.sets[set_id] = {"request": data, "gen": 0, "status": "UPLOADING"}
        photos = list(enumerate(data["photos"]))
        return self._json(201, {"captureSetId": set_id, "uploads": self._targets(set_id, 0, photos)})

    @staticmethod
    def _targets(set_id: str, gen: int, photos: list[tuple[int, dict]]) -> list[dict]:
        return [
            {"face": p["face"], "light": p["light"], "camera": p["camera"], "method": "PUT",
             "url": f"https://minio.test/trace-captures/{set_id}/{i}?g={gen}&X-Amz-Signature=s",
             "headers": {"Content-Type": p["contentType"]}, "expiresAt": "2026-11-06T10:10:00+09:00"}
            for i, p in photos
        ]

    @staticmethod
    def _key(p: dict) -> tuple[str, str, str]:
        return p["face"], p["light"], p["camera"]

    def _reissue(self, set_id: str, data: dict) -> Response:
        """contracts reissueUploadUrls: 아직 안 올라간 사진만 새 URL. 세트에 없는 키는 400, 닫힌 세트는 409."""
        s = self.sets[set_id]
        if s["status"] != "UPLOADING":
            return self._problem(409, "CAPTURE_SET_CLOSED")
        photos = list(enumerate(s["request"]["photos"]))
        index = {self._key(p): i for i, p in photos}
        asked = [self._key(p) for p in data.get("photos", [])]
        if any(k not in index for k in asked):
            return self._problem(400, "VALIDATION_FAILED")
        wanted = {index[k] for k in asked} if asked else {i for i, _ in photos}
        s["gen"] += 1
        left = [(i, p) for i, p in photos if i in wanted and f"{set_id}/{i}" not in self.objects]
        return self._json(200, {"uploads": self._targets(set_id, s["gen"], left)})

    def _detections(self, set_id: str, data: dict) -> Response:
        """contracts saveBoothDetections: 탐지마다 세트의 (face, light, camera) 한 장을 가리킨다.
        다시 보내면 그 세트의 부스 탐지를 통째로 바꾼다(modelVersion이 달라도). 등급이 매겨진 세트는 409. (A 11/2)"""
        s = self.sets[set_id]
        if s.get("graded"):
            return self._problem(409, "CAPTURE_SET_CLOSED")
        keys = {self._key(p) for p in s["request"]["photos"]}
        found = data.get("detections", [])
        if not data.get("modelVersion") or any(self._key(d) not in keys for d in found):
            return self._problem(400, "VALIDATION_FAILED")
        analyzed = {self._key(p) for p in data.get("analyzedPhotos", [])}
        if any(k not in keys for k in analyzed):  # 세트에 없는 사진은 범위와 상관없이 400
            return self._problem(400, "VALIDATION_FAILED")
        if s["request"].get("profile") == "QUICK" and analyzed != keys:  # 1.1.0: QUICK은 analyzedPhotos에 세트 사진 전부
            return self._problem(400, "VALIDATION_FAILED")
        if "analyzedPhotos" in data:  # FULL은 선택 필드
            s["analyzed"] = sorted(analyzed)
        self.detections[set_id] = list(found)
        s["inferenceMs"] = data.get("inferenceMs")
        s["boothTotalMs"] = data.get("boothTotalMs")
        return self._json(201, {"accepted": len(found)})

    def _put(self, url: str, headers: dict[str, str], body: bytes) -> Response:
        if self.fail_puts:
            self.fail_puts -= 1
            raise ConnectionResetError("가짜 연결 끊김")
        path, query = url.split("?")
        set_id, index = path.split("/")[-2:]
        gen = int(query.split("&")[0].removeprefix("g="))
        used = self.put_ok.get((set_id, gen), 0)
        if gen < self.sets[set_id]["gen"] or (gen < len(self.url_budget) and used >= self.url_budget[gen]):
            return Response(403, b"<Error><Code>AccessDenied</Code></Error>")
        self.put_ok[(set_id, gen)] = used + 1
        self.objects[f"{set_id}/{index}"] = body
        return Response(200)

    def _complete(self, set_id: str, data: dict, key: str) -> Response:
        if key in self.completed_keys:
            return self._json(202, self.completed_keys[key])
        s = self.sets[set_id]
        photos = s["request"]["photos"]
        skipped = data.get("skippedFaces", {})
        required = {"TOP", "BOT"} if s["request"].get("profile") == "QUICK" else {x.value for x in FACE_REQUIRED_BOOTH}
        if any(f in required for f in skipped):
            return self._problem(400, "VALIDATION_FAILED")
        missing = sorted(required - {p["face"] for p in photos})
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
