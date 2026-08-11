"""Naked decode cache: LRU, idle TTL, zlib/raw twin sharing."""

from __future__ import annotations

import struct
import time
import zlib

from pymergetic.wasmmod.cdn.services.naked_cache import NakedDecodeCache, install_naked_cache
from pymergetic.wasmmod.cdn_client.contents.mpzl import (
    install_unwrap_override,
    unwrap_mpzl,
    wrap_mpzl,
)


def _raw() -> bytes:
    return b"\x00asm\x01\x00\x00\x00" + (b"x" * 64)


def test_zlib_and_raw_share_one_entry() -> None:
    raw = _raw()
    z = wrap_mpzl(raw)
    cache = NakedDecodeCache(max_bytes=1024 * 1024, idle_ttl_s=60, max_entries=8)

    assert cache.unwrap(z) == raw
    st = cache.stats()
    assert st.misses == 1
    assert st.inserts == 1
    assert st.entries == 1
    assert st.bytes == len(raw)

    assert cache.unwrap(raw) == raw
    st = cache.stats()
    assert st.misses == 2  # first raw wire miss, then alias
    assert st.aliases == 1
    assert st.entries == 1
    assert st.inserts == 1

    assert cache.unwrap(z) == raw
    assert cache.stats().hits == 1


def test_raw_first_then_zlib_aliases() -> None:
    raw = _raw()
    z = wrap_mpzl(raw)
    cache = NakedDecodeCache(max_bytes=1024 * 1024, idle_ttl_s=60, max_entries=8)
    assert cache.unwrap(raw) == raw
    assert cache.unwrap(z) == raw
    st = cache.stats()
    assert st.entries == 1
    assert st.aliases == 1
    assert st.inserts == 1


def test_lru_eviction_by_entry_cap() -> None:
    cache = NakedDecodeCache(max_bytes=1024 * 1024, idle_ttl_s=0, max_entries=2)
    a = b"\x00asm" + b"a" * 16
    b = b"\x00asm" + b"b" * 16
    c = b"\x00asm" + b"c" * 16
    cache.unwrap(a)
    cache.unwrap(b)
    cache.unwrap(c)
    st = cache.stats()
    assert st.entries == 2
    assert st.evictions >= 1
    # a should be gone; touching b kept it
    assert cache.unwrap(b) == b
    assert cache.stats().hits >= 1


def test_idle_ttl_evicts() -> None:
    cache = NakedDecodeCache(max_bytes=1024 * 1024, idle_ttl_s=0.05, max_entries=8)
    raw = _raw()
    cache.unwrap(raw)
    time.sleep(0.08)
    assert cache.unwrap(raw) == raw
    st = cache.stats()
    assert st.evictions >= 1
    assert st.misses == 2


def test_disabled_when_max_bytes_zero() -> None:
    cache = NakedDecodeCache(max_bytes=0, idle_ttl_s=60, max_entries=8)
    assert not cache.enabled
    raw = _raw()
    assert cache.unwrap(wrap_mpzl(raw)) == raw
    assert cache.stats().entries == 0


def test_install_hooks_unwrap_mpzl() -> None:
    raw = _raw()
    z = wrap_mpzl(raw)
    cache = NakedDecodeCache(max_bytes=1024 * 1024, idle_ttl_s=60, max_entries=8)
    try:
        install_naked_cache(cache)
        assert unwrap_mpzl(z) == raw
        assert unwrap_mpzl(z) == raw
        assert cache.stats().hits == 1
    finally:
        install_unwrap_override(None)


def test_oversized_payload_not_cached() -> None:
    cache = NakedDecodeCache(max_bytes=32, idle_ttl_s=60, max_entries=8)
    raw = b"\x00asm" + b"y" * 64
    assert cache.unwrap(raw) == raw
    assert cache.stats().entries == 0
    assert cache.stats().inserts == 0


def test_mpzl_length_mismatch_still_raises() -> None:
    cache = NakedDecodeCache(max_bytes=1024, idle_ttl_s=60, max_entries=8)
    bad = b"MPZL" + struct.pack("<I", 99) + zlib.compress(b"hi", 9)
    try:
        cache.unwrap(bad)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
