"""MPZL whole-artifact zlib envelope."""

from __future__ import annotations

import re
import struct
import zlib
from collections.abc import Callable

from pymergetic.metal.cdn_client.contents.const import MPZL_MAGIC

_UnwrapOverride = Callable[[bytes], bytes]
_unwrap_override: _UnwrapOverride | None = None


def install_unwrap_override(fn: _UnwrapOverride | None) -> None:
    """Optional server hook (CDN naked decode cache). ``None`` clears."""
    global _unwrap_override
    _unwrap_override = fn


def unwrap_mpzl_raw(data: bytes) -> bytes:
    """Decode MPZL without any cache override (passthrough if not MPZL)."""
    if len(data) >= 8 and data[:4] == MPZL_MAGIC:
        (raw_len,) = struct.unpack_from("<I", data, 4)
        raw = zlib.decompress(data[8:])
        if len(raw) != raw_len:
            raise ValueError(f"MPZL length mismatch: got {len(raw)} want {raw_len}")
        return raw
    return data


def unwrap_mpzl(data: bytes) -> bytes:
    """Return naked artifact bytes (passthrough if not MPZL)."""
    if _unwrap_override is not None:
        return _unwrap_override(data)
    return unwrap_mpzl_raw(data)


def wrap_mpzl(data: bytes, *, level: int = 9) -> bytes:
    """Whole-artifact MPZL envelope (``MPZL`` | u32le raw_len | zlib)."""
    if len(data) >= 8 and data[:4] == MPZL_MAGIC:
        return data
    if len(data) > 0xFFFFFFFF:
        raise ValueError("artifact too large for MPZL")
    z = zlib.compress(data, level)
    return MPZL_MAGIC + struct.pack("<I", len(data)) + z


# Every naked lead artifact kind gets an MPZL twin — no half sets.
_NAKED_ARTIFACT = re.compile(r"^.+\.(?:wasm|aot\d*|elf|efi)$", re.IGNORECASE)


def ensure_zlib_artifacts(files: dict[str, bytes]) -> dict[str, bytes]:
    """Ensure MPZL ``.zlib`` twins for naked ``.wasm`` / ``.aotN`` / ``.elf`` / ``.efi``."""
    out: dict[str, bytes] = dict(files)
    for name, data in list(files.items()):
        if name.endswith(".zlib") or not _NAKED_ARTIFACT.match(name):
            continue
        zname = f"{name}.zlib"
        if zname not in out:
            out[zname] = wrap_mpzl(data)
    return out
