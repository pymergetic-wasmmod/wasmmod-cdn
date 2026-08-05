"""List container sections and extract by index."""
from __future__ import annotations

import struct
from typing import Literal

from pymergetic.metal.cdn_client.contents.const import (
    AOT_CUSTOM_SECTION_RAW,
    AOT_SECTION_NAMES,
    AOT_SECTION_TYPE_CUSTOM,
    WASM_SECTION_NAMES,
)
from pymergetic.metal.cdn_client.contents.models import ContainerSectionInfo
from pymergetic.metal.cdn_client.contents.mpzl import unwrap_mpzl
from pymergetic.metal.cdn_client.contents.section_extract import (
    _section_role_meta,
)
from pymergetic.metal.cdn_client.contents.section_typesigs import _read_uleb


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

