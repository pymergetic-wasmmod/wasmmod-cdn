"""Parse wasmmod.* custom-section payloads and strip signatures."""

from __future__ import annotations

import hashlib
import re
import struct

from pymergetic.metal.cdn_client.contents.const import (
    AOT_CUSTOM_SECTION_RAW,
    AOT_SECTION_TYPE_CUSTOM,
    DEPS_MAGIC,
    IMPORTS_MAGIC,
    KIND_NAMES,
    MPWS_MAGIC,
    MPWS_VER,
    PACK_MAGIC,
    SIG_SECTION,
    SOURCE_MAGIC,
)
from pymergetic.metal.cdn_client.contents.models import (
    DepInfo,
    ImportInfo,
    PackExportInfo,
    PackFileInfo,
    PackSectionInfo,
    SigCertInfo,
    SigSectionInfo,
    SourceFileInfo,
    SourceSectionInfo,
)
from pymergetic.metal.cdn_client.contents.sections import (
    _read_uleb,
    describe_binder_sig,
    extract_custom_section_elf,
)


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
    if len(buf) < 64 or buf[:4] != b"\x7fELF" or buf[4] != 2 or buf[5] != 1:
        return False
    shoff = struct.unpack_from("<Q", buf, 40)[0]
    shentsize = struct.unpack_from("<H", buf, 58)[0]
    shnum = struct.unpack_from("<H", buf, 60)[0]
    shstrndx = struct.unpack_from("<H", buf, 62)[0]
    if shentsize < 64 or shnum == 0 or shstrndx >= shnum:
        return False
    if shoff + shnum * shentsize > len(buf):
        return False
    shstr = buf[shoff + shstrndx * shentsize :]
    str_off = struct.unpack_from("<Q", shstr, 24)[0]
    str_sz = struct.unpack_from("<Q", shstr, 32)[0]
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
        sec_off = struct.unpack_from("<Q", sh, 24)[0]
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
        struct.pack_into("<Q", out, 40, old_shoff)
        struct.pack_into("<H", out, 60, old_shnum)
        struct.pack_into("<H", out, 62, old_shstrndx)
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
