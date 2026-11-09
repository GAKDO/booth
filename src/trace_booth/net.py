"""학교망 IPv6 함정 피하기(11/9).

학교망 DNS64는 gakdo.site에 IPv6(64:ff9b::…) 주소를 먼저 주는데 그 길이 막혀 있다. urllib은 주소를 차례로 시도하므로
IPv6 연결이 제한 시간(30초)을 다 쓴 뒤에야 IPv4로 넘어가, 사진 28장을 올리는 데 몇 분이 걸렸다(curl은 두 주소를
동시에 시도해 빨랐다). /etc/gai.conf를 고칠 sudo가 없어도 되게, 부스 프로세스 안에서만 IPv4 주소를 앞에 둔다.
IPv4 주소가 없으면 IPv6를 그대로 쓰므로 IPv6 전용 망에서도 동작은 같다.
"""

from __future__ import annotations

import socket
from collections.abc import Callable
from typing import Any

_original: Callable[..., list[Any]] | None = None


def ipv4_first(infos: list[Any]) -> list[Any]:
    """getaddrinfo 결과에서 IPv4를 앞으로(같은 종류 안의 순서는 그대로)."""
    return sorted(infos, key=lambda info: 0 if info[0] == socket.AF_INET else 1)


def prefer_ipv4() -> bool:
    """이 프로세스의 socket.getaddrinfo가 IPv4를 먼저 돌려주게 한다. 이미 켰으면 아무것도 하지 않고 False."""
    global _original
    if _original is not None:
        return False
    original = socket.getaddrinfo
    _original = original

    def getaddrinfo(*args: Any, **kwargs: Any) -> list[Any]:
        return ipv4_first(original(*args, **kwargs))

    socket.getaddrinfo = getaddrinfo  # type: ignore[assignment]
    return True


def restore() -> None:
    """테스트용: 원래 getaddrinfo로 되돌린다."""
    global _original
    if _original is not None:
        socket.getaddrinfo = _original  # type: ignore[assignment]
        _original = None
