"""테스트용 가짜 카메라."""

from __future__ import annotations

from trace_booth.camera import CameraError
from trace_booth.codes import Camera


class FakeCamera:
    """찍을 때마다 내용이 다른 JPEG 바이트를 돌려준다. fail=True면 CameraError."""

    def __init__(self, code: Camera) -> None:
        self.code = code
        self.fail = False
        self.count = 0
        self.settings_seen: list[dict[str, int]] = []

    def capture(self, settings: dict[str, int]) -> bytes:
        if self.fail:
            raise CameraError(self.code, "가짜 실패")
        self.count += 1
        self.settings_seen.append(settings)
        return b"\xff\xd8\xff\xe0" + f"{self.code.value}-{self.count}".encode() + b"\xff\xd9"


class FakeObjectStore:
    """메모리 저장소. online=False면 모든 호출이 ConnectionError."""

    def __init__(self) -> None:
        self.online = True
        self.objects: dict[str, tuple[bytes, dict[str, str]]] = {}
        self.puts = 0

    def head(self, key: str) -> dict[str, str] | None:
        self._check()
        return self.objects[key][1] if key in self.objects else None

    def put(self, key: str, path, metadata: dict[str, str]) -> None:
        self._check()
        self.puts += 1
        self.objects[key] = (path.read_bytes(), dict(metadata))

    def _check(self) -> None:
        if not self.online:
            raise ConnectionError("저장소에 닿지 않음")
