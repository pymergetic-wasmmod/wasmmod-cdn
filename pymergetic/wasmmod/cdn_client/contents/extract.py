"""Read embedded source/pack file bytes from an artifact."""

from __future__ import annotations

import struct
import zlib
from typing import Literal

from pymergetic.wasmmod.cdn_client.contents.const import (
    KIND_NAMES,
    PACK_MAGIC,
    PACK_SECTION,
    SOURCE_MAGIC,
    SOURCE_SECTION,
)
from pymergetic.wasmmod.cdn_client.contents.models import EmbeddedFileView
from pymergetic.wasmmod.cdn_client.contents.mpzl import unwrap_mpzl
from pymergetic.wasmmod.cdn_client.contents.parse import (
    parse_pack_payload,
    parse_source_payload,
)
from pymergetic.wasmmod.cdn_client.contents.sections import extract_custom_section


def _inflate(data: bytes, *, zlib_flag: bool, raw_len: int) -> bytes:
    if zlib_flag:
        out = zlib.decompress(data)
        if len(out) != raw_len:
            raise ValueError(f"bad inflated size: got {len(out)} want {raw_len}")
        return out
    return data


def _read_from_source_payload(payload: bytes, relpath: str) -> bytes | None:
    if len(payload) < 12 or payload[:4] != SOURCE_MAGIC:
        return None
    name_len = struct.unpack_from("<H", payload, 8)[0]
    i = 10 + name_len
    ver_len = struct.unpack_from("<H", payload, i)[0]
    i += 2 + ver_len
    n_tags = struct.unpack_from("<H", payload, i)[0]
    i += 2
    for _ in range(n_tags):
        kl = struct.unpack_from("<H", payload, i)[0]
        i += 2 + kl
        vl = struct.unpack_from("<H", payload, i)[0]
        i += 2 + vl
    n_files = struct.unpack_from("<I", payload, i)[0]
    i += 4
    for _ in range(n_files):
        pl = struct.unpack_from("<H", payload, i)[0]
        i += 2
        path = payload[i : i + pl].decode("utf-8")
        i += pl
        fflags = payload[i]
        i += 1
        raw_len, data_len = struct.unpack_from("<II", payload, i)
        i += 8
        blob = payload[i : i + data_len]
        i += data_len
        if path == relpath:
            return _inflate(blob, zlib_flag=bool(fflags & 1), raw_len=raw_len)
    return None


def _read_from_pack_payload(payload: bytes, relpath: str) -> tuple[bytes, str] | None:
    if len(payload) < 12 or payload[:4] != PACK_MAGIC:
        return None
    version = struct.unpack_from("<H", payload, 4)[0]
    name_len = struct.unpack_from("<H", payload, 8)[0]
    i = 10 + name_len
    n_files = struct.unpack_from("<I", payload, i)[0]
    i += 4
    for _ in range(n_files):
        pl = struct.unpack_from("<H", payload, i)[0]
        i += 2
        path = payload[i : i + pl].decode("utf-8")
        i += pl
        kind = payload[i]
        i += 1
        if version >= 3:
            fflags = payload[i]
            i += 1
            raw_len, data_len = struct.unpack_from("<II", payload, i)
            i += 8
            blob = payload[i : i + data_len]
            i += data_len
            if path == relpath:
                return (
                    _inflate(blob, zlib_flag=bool(fflags & 1), raw_len=raw_len),
                    KIND_NAMES.get(kind, str(kind)),
                )
        else:
            data_len = struct.unpack_from("<I", payload, i)[0]
            i += 4
            blob = payload[i : i + data_len]
            i += data_len
            if path == relpath:
                return blob, KIND_NAMES.get(kind, str(kind))
    return None


def extract_embedded_file(data: bytes, relpath: str) -> EmbeddedFileView:
    """Pull one embedded path from wasmmod.source (preferred) or wasmmod.pack."""
    body, section, kind, resolved = extract_embedded_bytes(data, relpath)
    return _to_file_view(resolved, section, kind, body)


def _list_embedded_paths(naked: bytes) -> list[str]:
    """All paths in wasmmod.source then wasmmod.pack (source first, unique)."""
    out: list[str] = []
    seen: set[str] = set()
    src_raw = extract_custom_section(naked, SOURCE_SECTION)
    if src_raw is not None:
        try:
            for f in parse_source_payload(src_raw).files:
                if f.path not in seen:
                    seen.add(f.path)
                    out.append(f.path)
        except ValueError:
            pass
    pack_raw = extract_custom_section(naked, PACK_SECTION)
    if pack_raw is not None:
        try:
            for f in parse_pack_payload(pack_raw).files:
                if f.path not in seen:
                    seen.add(f.path)
                    out.append(f.path)
        except ValueError:
            pass
    return out


def _resolve_embedded_relpath(naked: bytes, relpath: str) -> str:
    """Exact path, else unique basename / suffix match (DWARF often yields ``hello.c``)."""
    paths = _list_embedded_paths(naked)
    if relpath in paths:
        return relpath
    base = relpath.rsplit("/", 1)[-1]
    hits = [p for p in paths if p == base or p.endswith("/" + base)]
    if len(hits) == 1:
        return hits[0]
    if "/" in relpath:
        hits = [p for p in paths if p.endswith("/" + relpath)]
        if len(hits) == 1:
            return hits[0]
    raise FileNotFoundError(relpath)


def extract_embedded_bytes(
    data: bytes, relpath: str
) -> tuple[bytes, Literal["source", "pack"], str | None, str]:
    """Return ``(body, section, kind, resolved_path)`` for an embedded path."""
    if ".." in relpath.split("/") or relpath.startswith("/"):
        raise ValueError("invalid embedded path")
    naked = unwrap_mpzl(data)
    resolved = relpath
    src_raw = extract_custom_section(naked, SOURCE_SECTION)
    if src_raw is not None:
        body = _read_from_source_payload(src_raw, resolved)
        if body is not None:
            return body, "source", None, resolved
    pack_raw = extract_custom_section(naked, PACK_SECTION)
    if pack_raw is not None:
        hit = _read_from_pack_payload(pack_raw, resolved)
        if hit is not None:
            body, kind = hit
            return body, "pack", kind, resolved
    # Basename / suffix fallback (DWARF compile unit names).
    try:
        resolved = _resolve_embedded_relpath(naked, relpath)
    except FileNotFoundError:
        raise FileNotFoundError(relpath) from None
    if resolved == relpath:
        raise FileNotFoundError(relpath)
    if src_raw is not None:
        body = _read_from_source_payload(src_raw, resolved)
        if body is not None:
            return body, "source", None, resolved
    if pack_raw is not None:
        hit = _read_from_pack_payload(pack_raw, resolved)
        if hit is not None:
            body, kind = hit
            return body, "pack", kind, resolved
    raise FileNotFoundError(relpath)


def _to_file_view(
    path: str,
    section: Literal["source", "pack"],
    kind: str | None,
    body: bytes,
) -> EmbeddedFileView:
    text: str | None
    binary: bool
    try:
        text = body.decode("utf-8")
        if "\x00" in text:
            raise UnicodeError("nul")
        binary = False
    except UnicodeError:
        text = None
        binary = True
    return EmbeddedFileView(
        path=path,
        section=section,
        kind=kind,
        size=len(body),
        text=text,
        binary=binary,
    )
