"""학교망 IPv6 함정(11/9): 부스는 IPv4 주소를 먼저 시도한다."""

from __future__ import annotations

import socket

from trace_booth import net

V6 = (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("64:ff9b::dc43:d80c", 443, 0, 0))
V4 = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("220.67.216.12", 443))


def test_IPv6가_먼저_와도_IPv4를_앞에_둔다():
    assert net.ipv4_first([V6, V4]) == [V4, V6]


def test_IPv4가_없으면_IPv6를_그대로_쓴다():
    assert net.ipv4_first([V6]) == [V6]


def test_켜면_프로세스의_getaddrinfo가_IPv4를_먼저_돌려주고_두_번_켜도_한_번만_감싼다(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [V6, V4])
    try:
        assert net.prefer_ipv4() is True
        assert net.prefer_ipv4() is False
        assert socket.getaddrinfo("gakdo.site", 443) == [V4, V6]
    finally:
        net.restore()
