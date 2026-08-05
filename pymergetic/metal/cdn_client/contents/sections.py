"""Container section walk / extract / Wasm typesig helpers."""

from __future__ import annotations

import struct
from typing import Literal

from pymergetic.metal.cdn_client.contents.const import (
    AOT_CUSTOM_SECTION_RAW,
    AOT_SECTION_NAMES,
    AOT_SECTION_TYPE_CUSTOM,
    SECTION_RAW_LIMIT_CAP,
    SIG_AUTO,
    WASM_SECTION_NAMES,
)
from pymergetic.metal.cdn_client.contents.models import (
    ContainerSectionInfo,
    PackExportInfo,
    PackSectionInfo,
)
from pymergetic.metal.cdn_client.contents.mpzl import unwrap_mpzl


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




def extract_custom_section_wasm(wasm: bytes, section_name: str) -> bytes | None:
    if len(wasm) < 8 or wasm[:4] != b"\x00asm":
        return None
    name_b = section_name.encode("utf-8")
    i = 8
    try:
        while i < len(wasm):
            sid = wasm[i]
            i += 1
            size, i = _read_uleb(wasm, i)
            sec_end = i + size
            if sec_end > len(wasm):
                return None
            if sid == 0:
                nlen, j = _read_uleb(wasm, i)
                if j + nlen <= sec_end and wasm[j : j + nlen] == name_b:
                    return wasm[j + nlen : sec_end]
            i = sec_end
    except ValueError:
        return None
    return None


def extract_custom_section_aot(buf: bytes, section_name: str) -> bytes | None:
    if len(buf) < 8 or buf[:4] != b"\x00aot":
        return None
    want = section_name.encode("utf-8")
    p = 8
    while p + 8 <= len(buf):
        typ, size = struct.unpack_from("<II", buf, p)
        content = p + 8
        end = content + size
        if end > len(buf) or size > 0x10000000:
            break
        if typ == AOT_SECTION_TYPE_CUSTOM and size >= 6:
            sub = struct.unpack_from("<I", buf, content)[0]
            if sub == AOT_CUSTOM_SECTION_RAW:
                slen = struct.unpack_from("<H", buf, content + 4)[0]
                name_off = content + 6
                if name_off + slen <= end:
                    name_bytes = buf[name_off : name_off + slen]
                    bare = name_bytes[:-1] if name_bytes.endswith(b"\x00") else name_bytes
                    if bare == want:
                        return buf[name_off + slen : end]
        aligned = (end + 3) & ~3
        p = aligned if aligned <= len(buf) else end
    return None


def extract_custom_section_elf(buf: bytes, section_name: str) -> bytes | None:
    """ELF64 LE PROGBITS/NOTE named ``.wasmmod.*`` (or without leading dot)."""
    if len(buf) < 64 or buf[:4] != b"\x7fELF" or buf[4] != 2 or buf[5] != 1:
        return None
    shoff = struct.unpack_from("<Q", buf, 40)[0]
    shentsize = struct.unpack_from("<H", buf, 58)[0]
    shnum = struct.unpack_from("<H", buf, 60)[0]
    shstrndx = struct.unpack_from("<H", buf, 62)[0]
    if shentsize < 64 or shnum == 0 or shstrndx >= shnum:
        return None
    if shoff + shnum * shentsize > len(buf):
        return None
    shstr = buf[shoff + shstrndx * shentsize :]
    str_off = struct.unpack_from("<Q", shstr, 24)[0]
    str_sz = struct.unpack_from("<Q", shstr, 32)[0]
    if str_off + str_sz > len(buf):
        return None
    strtab = buf[str_off : str_off + str_sz]
    want = section_name.encode("utf-8")
    want_dot = want if want.startswith(b".") else (b"." + want)
    for i in range(shnum):
        sh = buf[shoff + i * shentsize :]
        name_off = struct.unpack_from("<I", sh, 0)[0]
        typ = struct.unpack_from("<I", sh, 4)[0]
        if typ not in (1, 7):  # PROGBITS / NOTE
            continue
        if name_off >= len(strtab):
            continue
        end = strtab.find(b"\x00", name_off)
        if end < 0:
            end = len(strtab)
        sname = strtab[name_off:end]
        if sname != want and sname != want_dot and sname.lstrip(b".") != want.lstrip(b"."):
            continue
        off = struct.unpack_from("<Q", sh, 24)[0]
        size = struct.unpack_from("<Q", sh, 32)[0]
        if off + size > len(buf) or size == 0:
            return None
        return buf[off : off + size]
    return None


def extract_custom_section(data: bytes, section_name: str) -> bytes | None:
    if len(data) < 4:
        return None
    if data[:4] == b"\x00asm":
        return extract_custom_section_wasm(data, section_name)
    if data[:4] == b"\x00aot":
        return extract_custom_section_aot(data, section_name)
    if data[:4] == b"\x7fELF":
        return extract_custom_section_elf(data, section_name)
    return None


def has_section(data: bytes, section_name: str) -> bool:
    return extract_custom_section(data, section_name) is not None


def _section_role_meta(name: str) -> bool:
    bare = name.lstrip(".")
    return bare.startswith("wasmmod.")


def _list_sections_wasm(wasm: bytes) -> list[ContainerSectionInfo]:
    if len(wasm) < 8 or wasm[:4] != b"\x00asm":
        return []
    out: list[ContainerSectionInfo] = []
    i = 8
    try:
        while i < len(wasm):
            sid = wasm[i]
            i += 1
            size, i = _read_uleb(wasm, i)
            sec_off = i
            sec_end = i + size
            if sec_end > len(wasm):
                break
            name = WASM_SECTION_NAMES.get(sid, f"section_{sid}")
            if sid == 0 and size > 0:
                try:
                    nlen, j = _read_uleb(wasm, sec_off)
                    if j + nlen <= sec_end:
                        name = wasm[j : j + nlen].decode("utf-8", errors="replace") or name
                except ValueError:
                    pass
            if sid == 10:
                role: Literal["code", "meta", "other"] = "code"
            elif _section_role_meta(name):
                role = "meta"
            else:
                role = "other"
            out.append(
                ContainerSectionInfo(
                    index=len(out),
                    name=name,
                    type_id=sid,
                    offset=sec_off,
                    size=size,
                    role=role,
                )
            )
            i = sec_end
    except ValueError:
        return out
    return out


def _list_sections_aot(buf: bytes) -> list[ContainerSectionInfo]:
    if len(buf) < 8 or buf[:4] != b"\x00aot":
        return []
    out: list[ContainerSectionInfo] = []
    p = 8
    while p + 8 <= len(buf):
        typ, size = struct.unpack_from("<II", buf, p)
        content = p + 8
        end = content + size
        if end > len(buf) or size > 0x10000000:
            break
        name = AOT_SECTION_NAMES.get(typ, f"type_{typ}")
        if typ == AOT_SECTION_TYPE_CUSTOM and size >= 6:
            sub = struct.unpack_from("<I", buf, content)[0]
            if sub == AOT_CUSTOM_SECTION_RAW:
                slen = struct.unpack_from("<H", buf, content + 4)[0]
                name_off = content + 6
                if name_off + slen <= end:
                    name_bytes = buf[name_off : name_off + slen]
                    bare = name_bytes[:-1] if name_bytes.endswith(b"\x00") else name_bytes
                    if bare:
                        name = bare.decode("utf-8", errors="replace")
        if typ == 2:
            role: Literal["code", "meta", "other"] = "code"
        elif _section_role_meta(name):
            role = "meta"
        else:
            role = "other"
        out.append(
            ContainerSectionInfo(
                index=len(out),
                name=name,
                type_id=typ,
                offset=content,
                size=size,
                role=role,
            )
        )
        aligned = (end + 3) & ~3
        p = aligned if aligned <= len(buf) else end
    return out


def _list_sections_elf(buf: bytes) -> list[ContainerSectionInfo]:
    if len(buf) < 64 or buf[:4] != b"\x7fELF" or buf[4] != 2 or buf[5] != 1:
        return []
    shoff = struct.unpack_from("<Q", buf, 40)[0]
    shentsize = struct.unpack_from("<H", buf, 58)[0]
    shnum = struct.unpack_from("<H", buf, 60)[0]
    shstrndx = struct.unpack_from("<H", buf, 62)[0]
    if shentsize < 64 or shnum == 0 or shstrndx >= shnum:
        return []
    if shoff + shnum * shentsize > len(buf):
        return []
    shstr = buf[shoff + shstrndx * shentsize :]
    str_off = struct.unpack_from("<Q", shstr, 24)[0]
    str_sz = struct.unpack_from("<Q", shstr, 32)[0]
    if str_off + str_sz > len(buf):
        return []
    strtab = buf[str_off : str_off + str_sz]
    out: list[ContainerSectionInfo] = []
    for i in range(shnum):
        sh = buf[shoff + i * shentsize :]
        name_off = struct.unpack_from("<I", sh, 0)[0]
        typ = struct.unpack_from("<I", sh, 4)[0]
        off = struct.unpack_from("<Q", sh, 24)[0]
        size = struct.unpack_from("<Q", sh, 32)[0]
        if size == 0:
            continue
        if off + size > len(buf):
            continue
        if name_off >= len(strtab):
            name = f"shdr_{i}"
        else:
            end = strtab.find(b"\x00", name_off)
            if end < 0:
                end = len(strtab)
            name = strtab[name_off:end].decode("utf-8", errors="replace") or f"shdr_{i}"
        if name == ".text" or name.startswith(".text."):
            role: Literal["code", "meta", "other"] = "code"
        elif _section_role_meta(name):
            role = "meta"
        else:
            role = "other"
        out.append(
            ContainerSectionInfo(
                # Real ELF shndx so it matches SymbolInfo.section_index / disasm.
                index=i,
                name=name,
                type_id=typ,
                offset=off,
                size=size,
                role=role,
            )
        )
    return out


def list_container_sections(data: bytes) -> list[ContainerSectionInfo]:
    """List all container sections on naked (or MPZL-wrapped) artifact bytes."""
    naked = unwrap_mpzl(data)
    if len(naked) < 4:
        return []
    if naked[:4] == b"\x00asm":
        return _list_sections_wasm(naked)
    if naked[:4] == b"\x00aot":
        return _list_sections_aot(naked)
    if naked[:4] == b"\x7fELF":
        return _list_sections_elf(naked)
    return []


def extract_container_section(data: bytes, *, index: int) -> bytes:
    """Return payload bytes for section ``index`` (from ``list_container_sections``)."""
    if index < 0:
        raise FileNotFoundError(f"section index {index}")
    naked = unwrap_mpzl(data)
    sections = list_container_sections(naked)
    sec = next((s for s in sections if s.index == index), None)
    if sec is None:
        raise FileNotFoundError(f"section index {index}")
    end = sec.offset + sec.size
    if end > len(naked):
        raise ValueError(f"section {index} truncated")
    return naked[sec.offset : end]

