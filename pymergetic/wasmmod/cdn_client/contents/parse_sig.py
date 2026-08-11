"""Signature strip / parse helpers for wasmmod artifacts."""
from __future__ import annotations

import hashlib
import struct

from pymergetic.wasmmod.cdn_client.contents.const import (
    AOT_CUSTOM_SECTION_RAW,
    AOT_SECTION_TYPE_CUSTOM,
    MPWS_MAGIC,
    MPWS_VER,
    SIG_SECTION,
)
from pymergetic.wasmmod.cdn_client.contents.models import SigCertInfo, SigSectionInfo
from pymergetic.wasmmod.cdn_client.contents.section_extract import extract_custom_section_elf
from pymergetic.wasmmod.cdn_client.contents.section_typesigs import _read_uleb


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def split_der_certs(blob: bytes) -> list[bytes]:
    """Split concatenated DER X.509 certificates (leaf first)."""
    out: list[bytes] = []
    i = 0
    while i < len(blob):
        if blob[i] != 0x30:
            raise ValueError(f"bad DER cert at offset {i}")
        if i + 1 >= len(blob):
            raise ValueError("truncated DER cert")
        length = blob[i + 1]
        hdr = 2
        if length & 0x80:
            n = length & 0x7F
            if n == 0 or i + 2 + n > len(blob):
                raise ValueError("bad DER length")
            length = int.from_bytes(blob[i + 2 : i + 2 + n], "big")
            hdr = 2 + n
        end = i + hdr + length
        if end > len(blob):
            raise ValueError("truncated DER cert")
        out.append(blob[i:end])
        i = end
    return out


def _elf_sig_offset_ge(buf: bytes, old_len: int) -> bool:
    """True if ``.wasmmod.sig`` (or bare name) has ``sh_offset >= old_len``."""
    if len(buf) < 52 or buf[:4] != b"\x7fELF" or buf[5] != 1:
        return False
    elfclass = buf[4]
    if elfclass == 2:
        if len(buf) < 64:
            return False
        shoff = struct.unpack_from("<Q", buf, 40)[0]
        shentsize = struct.unpack_from("<H", buf, 58)[0]
        shnum = struct.unpack_from("<H", buf, 60)[0]
        shstrndx = struct.unpack_from("<H", buf, 62)[0]
        min_sh, off_fmt, str_off_at, str_sz_at, sec_off_at = 64, "<Q", 24, 32, 24
    elif elfclass == 1:
        shoff = struct.unpack_from("<I", buf, 32)[0]
        shentsize = struct.unpack_from("<H", buf, 46)[0]
        shnum = struct.unpack_from("<H", buf, 48)[0]
        shstrndx = struct.unpack_from("<H", buf, 50)[0]
        min_sh, off_fmt, str_off_at, str_sz_at, sec_off_at = 40, "<I", 16, 20, 16
    else:
        return False
    if shentsize < min_sh or shnum == 0 or shstrndx >= shnum:
        return False
    if shoff + shnum * shentsize > len(buf):
        return False
    shstr = buf[shoff + shstrndx * shentsize :]
    str_off = struct.unpack_from(off_fmt, shstr, str_off_at)[0]
    str_sz = struct.unpack_from(off_fmt, shstr, str_sz_at)[0]
    if str_off + str_sz > len(buf):
        return False
    strtab = buf[str_off : str_off + str_sz]
    want = SIG_SECTION.encode("utf-8")
    want_dot = b"." + want if not want.startswith(b".") else want
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
        sec_off = struct.unpack_from(off_fmt, sh, sec_off_at)[0]
        return sec_off >= old_len
    return False


def without_sig_section(buf: bytes) -> bytes:
    """Artifact bytes ECDSA covers (omit ``wasmmod.sig``)."""
    if len(buf) < 8:
        raise ValueError("artifact too small")
    want = SIG_SECTION.encode("utf-8")
    if buf[:4] == b"\x00asm":
        out = bytearray(buf[:8])
        i = 8
        while i < len(buf):
            sec_start = i
            sid = buf[i]
            i += 1
            size, i = _read_uleb(buf, i)
            sec_end = i + size
            if sec_end > len(buf):
                raise ValueError("truncated wasm while stripping wasmmod.sig")
            skip = False
            if sid == 0:
                nlen, j = _read_uleb(buf, i)
                if j + nlen <= sec_end and buf[j : j + nlen] == want:
                    skip = True
            if not skip:
                out.extend(buf[sec_start:sec_end])
            i = sec_end
        return bytes(out)

    if buf[:4] == b"\x00aot":
        out = bytearray(buf[:8])
        p = 8
        while p + 8 <= len(buf):
            typ, size = struct.unpack_from("<II", buf, p)
            content = p + 8
            end = content + size
            if end > len(buf) or size > 0x10000000:
                raise ValueError("truncated aot while stripping wasmmod.sig")
            aligned = (end + 3) & ~3
            if aligned <= len(buf) and (aligned == len(buf) or aligned + 8 <= len(buf)):
                next_p = aligned
            else:
                next_p = min(end, len(buf))
            skip = False
            if typ == AOT_SECTION_TYPE_CUSTOM and size >= 6:
                sub = struct.unpack_from("<I", buf, content)[0]
                if sub == AOT_CUSTOM_SECTION_RAW:
                    slen = struct.unpack_from("<H", buf, content + 4)[0]
                    name_off = content + 6
                    if name_off + slen <= end:
                        name_bytes = buf[name_off : name_off + slen]
                        bare = name_bytes[:-1] if name_bytes.endswith(b"\x00") else name_bytes
                        if bare == want:
                            skip = True
            if not skip:
                out.extend(buf[p:next_p])
            p = next_p
            if skip:
                break
        return bytes(out)

    if buf[:4] == b"\x7fELF":
        # WPSE cookie restore (matches wasmmod tools/wasmmod_elf.py / wasmmod-read).
        wpse = struct.Struct("<4sQQHHI")
        cookie = None
        if len(buf) >= wpse.size and buf[-wpse.size : -wpse.size + 4] == b"WPSE":
            _magic, old_len, old_shoff, old_shnum, old_shstrndx, _pad = wpse.unpack_from(
                buf, len(buf) - wpse.size
            )
            if 0 < old_len <= len(buf) - wpse.size:
                cookie = (old_len, old_shoff, old_shnum, old_shstrndx)
        has_sig = extract_custom_section_elf(buf, SIG_SECTION) is not None
        if not has_sig:
            # Naked digest: drop trailing cookie if present.
            if cookie is not None:
                return buf[: len(buf) - wpse.size]
            return buf
        if cookie is None:
            raise ValueError("ELF strip of wasmmod.sig needs WPSE cookie")
        old_len, old_shoff, old_shnum, old_shstrndx = cookie
        # Require sig payload to live at/after old_len (last-append WPSE restore).
        if not _elf_sig_offset_ge(buf, old_len):
            raise ValueError("ELF strip of wasmmod.sig needs WPSE cookie")
        out = bytearray(buf[:old_len])
        if buf[4] == 2:  # ELFCLASS64
            struct.pack_into("<Q", out, 40, old_shoff)
            struct.pack_into("<H", out, 60, old_shnum)
            struct.pack_into("<H", out, 62, old_shstrndx)
        else:  # ELFCLASS32
            struct.pack_into("<I", out, 32, old_shoff)
            struct.pack_into("<H", out, 48, old_shnum)
            struct.pack_into("<H", out, 50, old_shstrndx)
        return bytes(out)

    raise ValueError("not a wasm/aot/elf artifact")


def parse_sig_payload(payload: bytes, *, naked: bytes) -> SigSectionInfo:
    """Parse ``wasmmod.sig`` payload into structured metadata."""
    if not payload:
        raise ValueError("empty wasmmod.sig payload")

    if len(payload) >= 8 and payload[:4] == MPWS_MAGIC and payload[4] == MPWS_VER:
        flags = payload[5]
        sl = int.from_bytes(payload[6:8], "big")
        if sl == 0 or 8 + sl > len(payload):
            raise ValueError("bad MPWS sig length")
        sig = payload[8 : 8 + sl]
        rest = payload[8 + sl :]
        chain = b""
        if len(rest) >= 2:
            cl = int.from_bytes(rest[0:2], "big")
            if 2 + cl > len(rest):
                raise ValueError("bad MPWS chain length")
            chain = rest[2 : 2 + cl]
        certs: list[SigCertInfo] = []
        if chain:
            for idx, der in enumerate(split_der_certs(chain)):
                certs.append(
                    SigCertInfo(
                        index=idx,
                        role="leaf" if idx == 0 else "intermediate",
                        der_len=len(der),
                        sha256=_sha256_hex(der),
                    )
                )
        try:
            signed_len = len(without_sig_section(naked))
        except ValueError:
            signed_len = 0
        return SigSectionInfo(
            format="mpws",
            version=MPWS_VER,
            flags=flags,
            payload_len=len(payload),
            sig_len=len(sig),
            sig_sha256=_sha256_hex(sig),
            chain_len=len(chain),
            signed_len=signed_len,
            certs=certs,
        )

    # Legacy raw ECDSA blob
    try:
        signed_len = len(without_sig_section(naked))
    except ValueError:
        signed_len = 0
    return SigSectionInfo(
        format="raw",
        version=None,
        flags=0,
        payload_len=len(payload),
        sig_len=len(payload),
        sig_sha256=_sha256_hex(payload),
        chain_len=0,
        signed_len=signed_len,
        certs=[],
    )

