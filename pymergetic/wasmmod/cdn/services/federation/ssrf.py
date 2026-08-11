"""SSRF guards for federation peer URLs."""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

_BLOCKED_HOSTS = frozenset(
    {
        "localhost",
        "metadata.google.internal",
        "metadata",
    }
)


def validate_peer_url(url: str, *, allow_private_net: bool = False) -> str:
    """Return normalized absolute http(s) URL or raise ValueError.

    Literal private/loopback IPs are rejected unless ``allow_private_net``.
    Hostnames are not DNS-resolved here (avoids flaky checks / rebinding races);
    obvious local names (``localhost``, ``*.localhost``) are still blocked.
    """
    v = (url or "").strip().rstrip("/")
    if not v.startswith(("http://", "https://")):
        raise ValueError("peer URL must be http(s)")
    parsed = urlparse(v)
    host = parsed.hostname
    if not host:
        raise ValueError("peer URL missing host")
    if parsed.username or parsed.password:
        raise ValueError("peer URL must not include credentials")
    if not allow_private_net:
        _reject_blocked_host(host)
    return v


def _reject_blocked_host(host: str) -> None:
    lowered = host.lower().rstrip(".")
    if lowered in _BLOCKED_HOSTS or lowered.endswith(".localhost"):
        raise ValueError(f"peer host not allowed: {host}")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return
    if _is_blocked_ip(ip):
        raise ValueError(f"peer IP not allowed: {host}")


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )
