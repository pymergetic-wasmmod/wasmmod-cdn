"""Parse pack / source / imports / deps custom-section payloads."""
from __future__ import annotations

import re
import struct

from pymergetic.metal.cdn_client.contents.const import (
    DEPS_MAGIC,
    IMPORTS_MAGIC,
    KIND_NAMES,
    PACK_MAGIC,
    SOURCE_MAGIC,
)
from pymergetic.metal.cdn_client.contents.models import (
    DepInfo,
    ImportInfo,
    PackExportInfo,
    PackFileInfo,
    PackSectionInfo,
    SourceFileInfo,
    SourceSectionInfo,
)
from pymergetic.metal.cdn_client.contents.section_typesigs import describe_binder_sig


def parse_pack_payload(payload: bytes) -> PackSectionInfo:
    if len(payload) < 12 or payload[:4] != PACK_MAGIC:
        raise ValueError("not a wasmmod.pack payload")
    version, flags = struct.unpack_from("<HH", payload, 4)
    name_len = struct.unpack_from("<H", payload, 8)[0]
    i = 10
    name = payload[i : i + name_len].decode("utf-8")
    i += name_len
    n_files = struct.unpack_from("<I", payload, i)[0]
    i += 4
    files: list[PackFileInfo] = []
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
            i += data_len
            files.append(
                PackFileInfo(
                    path=path,
                    kind=KIND_NAMES.get(kind, str(kind)),
                    raw_len=raw_len,
                    zlib=bool(fflags & 1),
                )
            )
        else:
            data_len = struct.unpack_from("<I", payload, i)[0]
            i += 4
            i += data_len
            files.append(
                PackFileInfo(
                    path=path,
                    kind=KIND_NAMES.get(kind, str(kind)),
                    raw_len=data_len,
                )
            )

    exports: list[PackExportInfo] = []
    if version >= 2 and i + 4 <= len(payload):
        n_exports = struct.unpack_from("<I", payload, i)[0]
        i += 4
        for _ in range(n_exports):
            parts: list[str] = []
            for _n in range(3):
                ln = struct.unpack_from("<H", payload, i)[0]
                i += 2
                parts.append(payload[i : i + ln].decode("utf-8"))
                i += ln
            sig = payload[i]
            i += 1
            exports.append(
                PackExportInfo(
                    module=parts[0],
                    func=parts[1],
                    export=parts[2],
                    sig=sig,
                    typesig=describe_binder_sig(sig),
                )
            )
    return PackSectionInfo(
        name=name,
        version=version,
        flags=flags,
        files=files,
        exports=exports,
    )


def parse_source_payload(payload: bytes) -> SourceSectionInfo:
    if len(payload) < 12 or payload[:4] != SOURCE_MAGIC:
        raise ValueError("not a wasmmod.source payload")
    version, flags = struct.unpack_from("<HH", payload, 4)
    name_len = struct.unpack_from("<H", payload, 8)[0]
    i = 10
    name = payload[i : i + name_len].decode("utf-8")
    i += name_len
    ver_len = struct.unpack_from("<H", payload, i)[0]
    i += 2
    pkg_version = payload[i : i + ver_len].decode("utf-8")
    i += ver_len
    n_tags = struct.unpack_from("<H", payload, i)[0]
    i += 2
    tags: dict[str, str] = {}
    for _ in range(n_tags):
        kl = struct.unpack_from("<H", payload, i)[0]
        i += 2
        k = payload[i : i + kl].decode("utf-8")
        i += kl
        vl = struct.unpack_from("<H", payload, i)[0]
        i += 2
        v = payload[i : i + vl].decode("utf-8")
        i += vl
        tags[k] = v
    n_files = struct.unpack_from("<I", payload, i)[0]
    i += 4
    files: list[SourceFileInfo] = []
    for _ in range(n_files):
        pl = struct.unpack_from("<H", payload, i)[0]
        i += 2
        path = payload[i : i + pl].decode("utf-8")
        i += pl
        fflags = payload[i]
        i += 1
        raw_len, data_len = struct.unpack_from("<II", payload, i)
        i += 8
        i += data_len
        files.append(SourceFileInfo(path=path, raw_len=raw_len, zlib=bool(fflags & 1)))
    return SourceSectionInfo(
        name=name,
        pkg_version=pkg_version,
        version=version,
        flags=flags,
        tags=tags,
        files=files,
    )


def parse_imports_payload(payload: bytes) -> list[ImportInfo]:
    if len(payload) < 10 or payload[:4] != IMPORTS_MAGIC:
        raise ValueError("not a wasmmod.imports payload")
    n = struct.unpack_from("<I", payload, 6)[0]
    i = 10
    out: list[ImportInfo] = []
    for _ in range(n):
        ml = struct.unpack_from("<H", payload, i)[0]
        i += 2
        mod = payload[i : i + ml].decode("utf-8")
        i += ml
        fl = struct.unpack_from("<H", payload, i)[0]
        i += 2
        func = payload[i : i + fl].decode("utf-8")
        i += fl
        out.append(ImportInfo(module=mod, func=func))
    return out


def parse_deps_payload(payload: bytes) -> list[DepInfo]:
    if len(payload) < 10 or payload[:4] != DEPS_MAGIC:
        raise ValueError("not a wasmmod.deps payload")
    n = struct.unpack_from("<I", payload, 6)[0]
    i = 10
    out: list[DepInfo] = []
    for _ in range(n):
        nl = struct.unpack_from("<H", payload, i)[0]
        i += 2
        name = payload[i : i + nl].decode("utf-8")
        i += nl
        vl = struct.unpack_from("<H", payload, i)[0]
        i += 2
        version = payload[i : i + vl].decode("utf-8")
        i += vl
        out.append(DepInfo(name=name, version=version))
    return out


def aot_version_from_filename(filename: str) -> int | None:
    m = re.search(r"\.aot(\d+)(?:\.zlib)?$", filename)
    if m:
        return int(m.group(1))
    return None
