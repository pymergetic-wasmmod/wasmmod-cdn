"""LRU + idle-TTL cache of decoded (naked) artifact bytes.

``.zlib`` (MPZL) and raw twins that decompress/passthrough to the same naked
payload share one entry: wire SHA-256 aliases point at the naked SHA-256 key.
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from pymergetic.metal.cdn_client.contents.mpzl import unwrap_mpzl_raw


@dataclass
class NakedCacheStats:
    hits: int = 0
    misses: int = 0
    inserts: int = 0
    evictions: int = 0
    aliases: int = 0
    bytes: int = 0
    entries: int = 0


@dataclass
class _Entry:
    naked: bytes
    size: int
    last_used: float


class NakedDecodeCache:
    """Process-local cache of naked artifact bytes."""

    def __init__(
        self,
        *,
        max_bytes: int = 256 * 1024 * 1024,
        idle_ttl_s: float = 600.0,
        max_entries: int = 256,
    ) -> None:
        self._max_bytes = max(0, int(max_bytes))
        self._idle_ttl_s = max(0.0, float(idle_ttl_s))
        self._max_entries = max(0, int(max_entries))
        self._by_naked: OrderedDict[str, _Entry] = OrderedDict()
        self._wire_to_naked: dict[str, str] = {}
        self._total = 0
        self._hits = 0
        self._misses = 0
        self._inserts = 0
        self._evictions = 0
        self._aliases = 0
        self._lock = threading.RLock()

    @property
    def enabled(self) -> bool:
        return self._max_bytes > 0 and self._max_entries > 0

    def stats(self) -> NakedCacheStats:
        with self._lock:
            return NakedCacheStats(
                hits=self._hits,
                misses=self._misses,
                inserts=self._inserts,
                evictions=self._evictions,
                aliases=self._aliases,
                bytes=self._total,
                entries=len(self._by_naked),
            )

    def clear(self) -> None:
        with self._lock:
            self._by_naked.clear()
            self._wire_to_naked.clear()
            self._total = 0

    def unwrap(self, data: bytes) -> bytes:
        """Return naked bytes, caching / aliasing wire digests when enabled."""
        if not self.enabled:
            return unwrap_mpzl_raw(data)

        wire_hash = hashlib.sha256(data).hexdigest()
        with self._lock:
            self._purge_expired_unlocked()
            hit = self._lookup_unlocked(wire_hash)
            if hit is not None:
                self._hits += 1
                return hit
            self._misses += 1

        naked = unwrap_mpzl_raw(data)
        naked_hash = hashlib.sha256(naked).hexdigest()

        with self._lock:
            hit = self._lookup_unlocked(wire_hash)
            if hit is not None:
                self._hits += 1
                return hit
            existing = self._by_naked.get(naked_hash)
            if existing is not None:
                if self._expired(existing):
                    self._evict_naked_unlocked(naked_hash)
                else:
                    self._touch_unlocked(naked_hash, existing)
                    self._wire_to_naked[wire_hash] = naked_hash
                    self._aliases += 1
                    return existing.naked
            self._insert_unlocked(wire_hash, naked_hash, naked)
            return naked

    def _lookup_unlocked(self, wire_hash: str) -> bytes | None:
        naked_hash = self._wire_to_naked.get(wire_hash)
        if naked_hash is None:
            return None
        entry = self._by_naked.get(naked_hash)
        if entry is None:
            self._wire_to_naked.pop(wire_hash, None)
            return None
        if self._expired(entry):
            self._evict_naked_unlocked(naked_hash)
            return None
        self._touch_unlocked(naked_hash, entry)
        return entry.naked

    def _touch_unlocked(self, naked_hash: str, entry: _Entry) -> None:
        entry.last_used = time.monotonic()
        self._by_naked.move_to_end(naked_hash)

    def _expired(self, entry: _Entry) -> bool:
        if self._idle_ttl_s <= 0:
            return False
        return (time.monotonic() - entry.last_used) > self._idle_ttl_s

    def _purge_expired_unlocked(self) -> None:
        if self._idle_ttl_s <= 0:
            return
        now = time.monotonic()
        dead = [
            key
            for key, entry in self._by_naked.items()
            if (now - entry.last_used) > self._idle_ttl_s
        ]
        for key in dead:
            self._evict_naked_unlocked(key)

    def _insert_unlocked(self, wire_hash: str, naked_hash: str, naked: bytes) -> None:
        size = len(naked)
        if size > self._max_bytes:
            return
        while self._by_naked and (
            self._total + size > self._max_bytes
            or len(self._by_naked) >= self._max_entries
        ):
            old_key, _old = self._by_naked.popitem(last=False)
            self._drop_entry_unlocked(old_key, _old)

        self._by_naked[naked_hash] = _Entry(
            naked=naked, size=size, last_used=time.monotonic()
        )
        self._by_naked.move_to_end(naked_hash)
        self._total += size
        self._wire_to_naked[wire_hash] = naked_hash
        self._inserts += 1

    def _evict_naked_unlocked(self, naked_hash: str) -> None:
        entry = self._by_naked.pop(naked_hash, None)
        if entry is None:
            return
        self._drop_entry_unlocked(naked_hash, entry)

    def _drop_entry_unlocked(self, naked_hash: str, entry: _Entry) -> None:
        self._total = max(0, self._total - entry.size)
        self._evictions += 1
        dead_wires = [w for w, n in self._wire_to_naked.items() if n == naked_hash]
        for w in dead_wires:
            self._wire_to_naked.pop(w, None)


_ACTIVE: NakedDecodeCache | None = None


def active_naked_cache() -> NakedDecodeCache | None:
    return _ACTIVE


def install_naked_cache(cache: NakedDecodeCache | None) -> None:
    """Hook ``unwrap_mpzl`` so inspect/files share the server decode cache."""
    global _ACTIVE
    from pymergetic.metal.cdn_client.contents import mpzl as mpzl_mod

    _ACTIVE = cache if cache is not None and cache.enabled else None
    if _ACTIVE is None:
        mpzl_mod.install_unwrap_override(None)
        return
    mpzl_mod.install_unwrap_override(_ACTIVE.unwrap)


def naked_cache_from_settings(settings: Any) -> NakedDecodeCache:
    return NakedDecodeCache(
        max_bytes=int(getattr(settings, "naked_cache_max_bytes", 0) or 0),
        idle_ttl_s=float(getattr(settings, "naked_cache_idle_ttl_s", 600.0) or 0.0),
        max_entries=int(getattr(settings, "naked_cache_max_entries", 256) or 0),
    )
