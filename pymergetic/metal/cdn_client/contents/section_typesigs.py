"""Wasm typesig / binder helpers + byte slicing."""
from __future__ import annotations

import struct

from pymergetic.metal.cdn_client.contents.const import SECTION_RAW_LIMIT_CAP, SIG_AUTO
from pymergetic.metal.cdn_client.contents.models import PackExportInfo, PackSectionInfo


def slice_bytes(
    body: bytes, *, offset: int = 0, limit: int | None = None
) -> bytes:
    """Slice ``body[offset:offset+limit]`` with a hard cap on ``limit``."""
    if offset < 0:
        raise ValueError("offset must be >= 0")
    if offset > len(body):
        return b""
    if limit is None:
        return body[offset:]
    if limit < 0:
        raise ValueError("limit must be >= 0")
    capped = min(int(limit), SECTION_RAW_LIMIT_CAP)
    return body[offset : offset + capped]


def _read_uleb(buf: bytes, i: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while i < len(buf):
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        if (b & 0x80) == 0:
            return result, i
        shift += 7
        if shift > 35:
            break
    raise ValueError("truncated uleb128")


_VALTYPE = {0x7F: "i32", 0x7E: "i64", 0x7D: "f32", 0x7C: "f64", 0x7B: "v128"}
def describe_binder_sig(tag: int) -> str:
    """Decode pack binder tag (not crypto). 255 = loader introspects Wasm types."""
    if tag == SIG_AUTO:
        return "auto"
    if 0 <= tag <= 8:
        params = ", ".join(["i32"] * tag)
        return f"({params}) -> i32"
    return f"tag:{tag}"


def _fmt_functype(params: list[str], results: list[str]) -> str:
    p = ", ".join(params)
    if not results:
        return f"({p})"
    if len(results) == 1:
        return f"({p}) -> {results[0]}"
    return f"({p}) -> ({', '.join(results)})"


def _read_valtypes(buf: bytes, i: int) -> tuple[list[str], int]:
    n, i = _read_uleb(buf, i)
    out: list[str] = []
    for _ in range(n):
        if i >= len(buf):
            raise ValueError("truncated valtype vector")
        out.append(_VALTYPE.get(buf[i], f"0x{buf[i]:02x}"))
        i += 1
    return out, i


def wasm_func_export_typesigs(wasm: bytes) -> dict[str, str]:
    """Map Wasm export name → ``(i32, i64) -> i32`` for function exports."""
    if len(wasm) < 8 or wasm[:4] != b"\x00asm":
        return {}
    types: list[tuple[list[str], list[str]]] = []
    func_type_idxs: list[int] = []
    import_func_count = 0
    exports: list[tuple[str, int]] = []  # name, func_index

    i = 8
    try:
        while i < len(wasm):
            sid = wasm[i]
            i += 1
            size, i = _read_uleb(wasm, i)
            sec_end = i + size
            if sec_end > len(wasm):
                break
            body = i
            if sid == 1:  # Type
                n, j = _read_uleb(wasm, body)
                for _ in range(n):
                    if wasm[j] != 0x60:
                        raise ValueError("expected functype")
                    j += 1
                    params, j = _read_valtypes(wasm, j)
                    results, j = _read_valtypes(wasm, j)
                    types.append((params, results))
            elif sid == 2:  # Import
                n, j = _read_uleb(wasm, body)
                for _ in range(n):
                    ml, j = _read_uleb(wasm, j)
                    j += ml
                    fl, j = _read_uleb(wasm, j)
                    j += fl
                    kind = wasm[j]
                    j += 1
                    if kind == 0:  # func
                        _, j = _read_uleb(wasm, j)
                        import_func_count += 1
                    elif kind == 1:  # table
                        j += 1  # reftype
                        flags = wasm[j]
                        j += 1
                        _, j = _read_uleb(wasm, j)
                        if flags & 1:
                            _, j = _read_uleb(wasm, j)
                    elif kind == 2:  # mem
                        flags = wasm[j]
                        j += 1
                        _, j = _read_uleb(wasm, j)
                        if flags & 1:
                            _, j = _read_uleb(wasm, j)
                    elif kind == 3:  # global
                        j += 1  # valtype
                        j += 1  # mut
                    else:
                        break
            elif sid == 3:  # Function
                n, j = _read_uleb(wasm, body)
                for _ in range(n):
                    ti, j = _read_uleb(wasm, j)
                    func_type_idxs.append(ti)
            elif sid == 7:  # Export
                n, j = _read_uleb(wasm, body)
                for _ in range(n):
                    nl, j = _read_uleb(wasm, j)
                    name = wasm[j : j + nl].decode("utf-8", errors="replace")
                    j += nl
                    kind = wasm[j]
                    j += 1
                    idx, j = _read_uleb(wasm, j)
                    if kind == 0:
                        exports.append((name, idx))
            i = sec_end
    except (ValueError, IndexError, struct.error):
        return {}

    out: dict[str, str] = {}
    for name, fidx in exports:
        local = fidx - import_func_count
        if local < 0 or local >= len(func_type_idxs):
            continue
        ti = func_type_idxs[local]
        if ti < 0 or ti >= len(types):
            continue
        params, results = types[ti]
        out[name] = _fmt_functype(params, results)
    return out


def enrich_pack_export_typesigs(pack: PackSectionInfo, naked: bytes) -> PackSectionInfo:
    """Fill ``typesig`` from Wasm types when possible, else binder-tag decode."""
    resolved = wasm_func_export_typesigs(naked) if naked[:4] == b"\x00asm" else {}
    exports: list[PackExportInfo] = []
    for ex in pack.exports:
        typesig = (
            resolved.get(ex.export)
            or resolved.get(ex.func)
            or describe_binder_sig(ex.sig)
        )
        exports.append(ex.model_copy(update={"typesig": typesig}))
    return pack.model_copy(update={"exports": exports})



