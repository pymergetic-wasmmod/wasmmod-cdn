"""Extract named custom sections from wasm / aot / elf."""
from __future__ import annotations

import struct

from pymergetic.wasmmod.cdn_client.contents.const import (
    AOT_CUSTOM_SECTION_RAW,
    AOT_SECTION_TYPE_CUSTOM,
)
from pymergetic.wasmmod.cdn_client.contents.section_typesigs import _read_uleb


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
    """ELF32/ELF64 LE PROGBITS/NOTE named ``.wasmmod.*`` (or without leading dot)."""
    if len(buf) < 52 or buf[:4] != b"\x7fELF" or buf[5] != 1:
        return None
    elfclass = buf[4]
    if elfclass == 2:  # ELFCLASS64
        if len(buf) < 64:
            return None
        shoff = struct.unpack_from("<Q", buf, 40)[0]
        shentsize = struct.unpack_from("<H", buf, 58)[0]
        shnum = struct.unpack_from("<H", buf, 60)[0]
        shstrndx = struct.unpack_from("<H", buf, 62)[0]
        min_shentsize = 64
        off_fmt, size_fmt, str_off_at, str_sz_at = "<Q", "<Q", 24, 32
    elif elfclass == 1:  # ELFCLASS32 (BIOS trampoline)
        shoff = struct.unpack_from("<I", buf, 32)[0]
        shentsize = struct.unpack_from("<H", buf, 46)[0]
        shnum = struct.unpack_from("<H", buf, 48)[0]
        shstrndx = struct.unpack_from("<H", buf, 50)[0]
        min_shentsize = 40
        off_fmt, size_fmt, str_off_at, str_sz_at = "<I", "<I", 16, 20
    else:
        return None
    if shentsize < min_shentsize or shnum == 0 or shstrndx >= shnum:
        return None
    if shoff + shnum * shentsize > len(buf):
        return None
    shstr = buf[shoff + shstrndx * shentsize :]
    str_off = struct.unpack_from(off_fmt, shstr, str_off_at)[0]
    str_sz = struct.unpack_from(size_fmt, shstr, str_sz_at)[0]
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
        off = struct.unpack_from(off_fmt, sh, 24 if elfclass == 2 else 16)[0]
        size = struct.unpack_from(size_fmt, sh, 32 if elfclass == 2 else 20)[0]
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
