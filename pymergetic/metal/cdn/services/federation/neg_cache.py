"""Short-TTL negative cache for federation peer 404s."""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlencode


class NegativePeerCache:
    """Remember peer misses so we don't re-fanout hot 404s."""

    def __init__(self, ttl_s: float = 15.0) -> None:
        self._ttl = ttl_s
        self._hits: dict[str, float] = {}

    @staticmethod
    def key(
        *,
        mount_id: str,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> str:
        q = ""
        if params:
            items = sorted((str(k), str(v)) for k, v in params.items())
            q = "?" + urlencode(items)
        return f"{mount_id}:{method.upper()}:{path}{q}"

    def is_miss(self, key: str) -> bool:
        exp = self._hits.get(key)
        if exp is None:
            return False
        if time.monotonic() > exp:
            self._hits.pop(key, None)
            return False
        return True

    def remember_miss(self, key: str) -> None:
        self._hits[key] = time.monotonic() + self._ttl

    def invalidate_mount(self, mount_id: str) -> None:
        prefix = f"{mount_id}:"
        for k in [k for k in self._hits if k.startswith(prefix)]:
            self._hits.pop(k, None)

    def clear(self) -> None:
        self._hits.clear()


def neg_cache_from_request(request: Any) -> NegativePeerCache:
    """Get or create the app-scoped negative cache."""
    cache = getattr(request.app.state, "federation_neg_cache", None)
    if cache is None:
        cache = NegativePeerCache()
        request.app.state.federation_neg_cache = cache
    return cache
