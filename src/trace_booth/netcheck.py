"""`trace-booth netcheck`: 서버 주소마다 연결 시간을 재고, IPv4 먼저 연결로 한 번 불러 본다(업로드가 느릴 때).

11/9 학교망에서 gakdo.site의 IPv6(64:ff9b::…)가 막혀 연결마다 제한 시간을 다 쓴 일이 있었다. 이 명령으로 새 부스에서도
어느 주소가 막혔는지, 요청 한 번이 몇 초 걸리는지 바로 본다. 비밀·토큰은 쓰지 않는다.
"""

from __future__ import annotations

import socket
import time
from collections.abc import Callable
from urllib.parse import urlsplit

from .capture_client import CONNECT_SECONDS, HttpCall

Clock = Callable[[], float]


def probe(host: str, port: int, clock: Clock = time.perf_counter) -> list[tuple[str, float | None, str]]:
    """DNS가 준 순서대로 주소마다 (주소, 연결 초 또는 None, 오류)."""
    out: list[tuple[str, float | None, str]] = []
    seen: set[str] = set()
    for family, kind, proto, _, sockaddr in socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM):
        addr = str(sockaddr[0])
        if addr in seen:
            continue
        seen.add(addr)
        sock = socket.socket(family, kind, proto)
        sock.settimeout(CONNECT_SECONDS)
        start = clock()
        try:
            sock.connect(sockaddr)
        except OSError as e:
            out.append((addr, None, type(e).__name__))
        else:
            out.append((addr, clock() - start, ""))
        finally:
            sock.close()
    return out


def netcheck(url: str, http: HttpCall, clock: Clock = time.perf_counter) -> int:
    parts = urlsplit(url)
    host = parts.hostname or ""
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        rows = probe(host, port, clock)
    except OSError as e:
        print(f"{host}: 주소를 찾지 못함({type(e).__name__})")
        return 1
    for addr, seconds, error in rows:
        kind = "IPv6" if ":" in addr else "IPv4"
        print(f"{kind} {addr}: " + (f"{seconds * 1000:.0f}ms" if seconds is not None else f"실패({error}, {CONNECT_SECONDS}초 제한)"))
    start = clock()
    try:
        status = http("GET", url, {}, None).status
    except OSError as e:
        print(f"요청 실패: {type(e).__name__}")
        return 1
    print(f"요청 한 번(IPv4 먼저): HTTP {status}, {clock() - start:.2f}초")
    return 0 if any(s is not None for _, s, _ in rows) else 1
