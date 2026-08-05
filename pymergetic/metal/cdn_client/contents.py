"""Inspect wasmmod .wasm / .aot / .elf (and MPZL .zlib) for CDN index ``contents``.

Shared by metal-cdn (on upload) and host tools. Inventory / metadata only —
file bodies are not embedded. Models are Pydantic so index JSON stays typed.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import re
import struct
import sys
import zlib
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

MPZL_MAGIC = b"MPZL"
PACK_MAGIC = b"MPWP"
SOURCE_MAGIC = b"MPSR"
IMPORTS_MAGIC = b"MPWI"
DEPS_MAGIC = b"MPWD"
MPWS_MAGIC = b"MPWS"
MPWS_VER = 1
PACK_SECTION = "wasmmod.pack"
SOURCE_SECTION = "wasmmod.source"
IMPORTS_SECTION = "wasmmod.imports"
DEPS_SECTION = "wasmmod.deps"
SIG_SECTION = "wasmmod.sig"

AOT_SECTION_TYPE_CUSTOM = 100
AOT_CUSTOM_SECTION_RAW = 0
# WAMR AOTSectionType (aot_runtime.h) — used for display names / role only.
AOT_SECTION_NAMES: dict[int, str] = {
    0: "target_info",
    1: "init_data",
    2: "text",
    3: "function",
    4: "export",
    5: "relocation",
    6: "signature",
    AOT_SECTION_TYPE_CUSTOM: "custom",
}

WASM_SECTION_NAMES: dict[int, str] = {
    0: "custom",
    1: "type",
    2: "import",
    3: "function",
    4: "table",
    5: "memory",
    6: "global",
    7: "export",
    8: "start",
    9: "element",
    10: "code",
    11: "data",
    12: "datacount",
}

KIND_NAMES = {1: "py", 2: "mpy", 3: "raw", 4: "pyc"}


class ArtifactBinaryKind(str, Enum):
    WASM = "wasm"
    AOT = "aot"
    ELF = "elf"
    UNKNOWN = "unknown"


class ArtifactEnvelope(str, Enum):
    RAW = "raw"
    MPZL = "mpzl"


class PackFileInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    kind: str
    raw_len: int = Field(ge=0)
    zlib: bool = False


class PackExportInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    module: str
    func: str
    export: str
    # Binder tag from pack.toml (0–8 = N×i32→i32 legacy; 255 = SIG_AUTO).
    sig: int = Field(ge=0, le=255)
    # Human Wasm type string, e.g. ``(i32, i32) -> i32`` (from module when possible).
    typesig: str = ""


class PackSectionInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    version: int = Field(ge=1)
    flags: int = 0
    files: list[PackFileInfo] = Field(default_factory=list)
    exports: list[PackExportInfo] = Field(default_factory=list)


class SourceFileInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    raw_len: int = Field(ge=0)
    zlib: bool = False


class SourceSectionInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    pkg_version: str
    version: int = Field(ge=1)
    flags: int = 0
    tags: dict[str, str] = Field(default_factory=dict)
    files: list[SourceFileInfo] = Field(default_factory=list)


class ImportInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    module: str
    func: str


class DepInfo(BaseModel):
    """CDN/install dependency from ``wasmmod.deps`` (exact version)."""

    model_config = ConfigDict(extra="forbid")

    name: str
    version: str


class SigCertInfo(BaseModel):
    """One DER certificate from the MPWS chain (leaf first)."""

    model_config = ConfigDict(extra="forbid")

    index: int = Field(ge=0)
    role: Literal["leaf", "intermediate"] = "leaf"
    der_len: int = Field(ge=0)
    sha256: str


class SigSectionInfo(BaseModel):
    """Parsed ``wasmmod.sig`` (MPWS envelope or legacy raw ECDSA)."""

    model_config = ConfigDict(extra="forbid")

    format: Literal["mpws", "raw"] = "mpws"
    version: int | None = Field(default=None, ge=1)
    flags: int = 0
    payload_len: int = Field(ge=0)
    sig_len: int = Field(ge=0)
    sig_sha256: str = ""
    chain_len: int = Field(default=0, ge=0)
    signed_len: int = Field(default=0, ge=0)
    certs: list[SigCertInfo] = Field(default_factory=list)


class ContainerSectionInfo(BaseModel):
    """One container section in a naked ``.wasm`` / ``.aot`` / ``.elf``."""

    model_config = ConfigDict(extra="forbid")

    index: int = Field(ge=0)
    name: str
    type_id: int = Field(ge=0)
    offset: int = Field(ge=0)
    size: int = Field(ge=0)
    role: Literal["code", "meta", "other"] = "other"


class SymbolInfo(BaseModel):
    """ELF symtab / Wasm export entry (from wasmmod_inspect)."""

    model_config = ConfigDict(extra="forbid")

    name: str
    section_index: int | None = None
    offset: int = Field(default=0, ge=0)
    size: int = Field(default=0, ge=0)
    kind: str = "other"
    binding: str = ""


class ArtifactContents(BaseModel):
    """Per-file inspect result (one uploaded .wasm / .aot / .elf / .zlib)."""

    model_config = ConfigDict(extra="forbid")

    filename: str = ""
    kind: ArtifactBinaryKind = ArtifactBinaryKind.UNKNOWN
    encoding: ArtifactEnvelope = ArtifactEnvelope.RAW
    size: int = Field(default=0, ge=0)
    naked_size: int = Field(default=0, ge=0)
    signed: bool = False
    aot_version: int | None = Field(default=None, ge=1)
    pack: PackSectionInfo | None = None
    source: SourceSectionInfo | None = None
    sig: SigSectionInfo | None = None
    imports: list[ImportInfo] = Field(default_factory=list)
    deps: list[DepInfo] = Field(default_factory=list)
    sections: list[ContainerSectionInfo] = Field(default_factory=list)
    symbols: list[SymbolInfo] = Field(default_factory=list)
    has_dwarf: bool = False
    error: str | None = None
    pack_error: str | None = None
    source_error: str | None = None
    imports_error: str | None = None
    deps_error: str | None = None
    sig_error: str | None = None


def _wasmmod_inspect_mod() -> Any | None:
    """Load shared inspect helpers from ``pymergetic-wasmmod-tools`` (or legacy paths)."""
    try:
        from pymergetic.wasmmod.tools import inspect as wasmmod_inspect

        return wasmmod_inspect
    except ImportError:
        pass

    try:
        import wasmmod_inspect  # type: ignore

        return wasmmod_inspect
    except ImportError:
        pass

    here = Path(__file__).resolve()
    candidates: list[Path] = [
        here.parent / "wasmmod_tools",  # legacy bundled copy
    ]
    env = (os.environ.get("WASMMOD_TOOLS") or "").strip()
    if env:
        candidates.append(Path(env))
    candidates.append(Path("/opt/wasmmod-tools"))
    for idx in (4, 5, 6):
        if len(here.parents) > idx:
            candidates.append(here.parents[idx] / "wasmmod-tools" / "src")
            candidates.append(
                here.parents[idx] / "metalpython" / "extmod" / "wasmmod" / "tools"
            )
    candidates.append(Path.home() / "Devel/os-sdk/packages/wasmmod-tools" / "src")
    candidates.append(
        Path.home() / "Devel/os-sdk/packages/metalpython/extmod/wasmmod/tools"
    )

    for tools in candidates:
        # Package src layout
        if (tools / "pymergetic" / "wasmmod" / "tools" / "inspect.py").is_file():
            if str(tools) not in sys.path:
                sys.path.insert(0, str(tools))
            try:
                from pymergetic.wasmmod.tools import inspect as wasmmod_inspect

                return wasmmod_inspect
            except ImportError:
                pass
        path = tools / "wasmmod_inspect.py"
        if not path.is_file():
            continue
        if str(tools) not in sys.path:
            sys.path.insert(0, str(tools))
        try:
            import wasmmod_inspect  # type: ignore

            return wasmmod_inspect
        except ImportError:
            pass
        spec = importlib.util.spec_from_file_location("wasmmod_inspect", path)
        if spec is None or spec.loader is None:
            continue
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        try:
            spec.loader.exec_module(mod)
        except Exception:
            sys.modules.pop(spec.name, None)
            continue
        return mod
    return None


def list_pack_symbols(data: bytes) -> list[SymbolInfo]:
    """Symbols for naked pack bytes (ELF/Wasm). Empty if inspect helper missing."""
    mod = _wasmmod_inspect_mod()
    if mod is None:
        return []
    try:
        naked = unwrap_mpzl(data)
    except ValueError:
        naked = data
    out: list[SymbolInfo] = []
    try:
        syms = mod.list_symbols(naked)
    except Exception:
        return []
    for s in syms:
        out.append(
            SymbolInfo(
                name=s.name,
                section_index=s.section_index,
                offset=int(s.offset),
                size=int(s.size),
                kind=str(s.kind),
                binding=str(s.binding or ""),
            )
        )
    return out


def pack_has_dwarf(data: bytes) -> bool:
    mod = _wasmmod_inspect_mod()
    if mod is None:
        return False
    try:
        naked = unwrap_mpzl(data)
    except ValueError:
        naked = data
    return bool(mod.has_dwarf(naked))


class LocationInfo(BaseModel):
    """Source / DWARF / symbol location from wasmmod_inspect."""

    model_config = ConfigDict(extra="forbid")

    path: str
    line: int | None = None
    role: str = "dwarf"


class DisasmLineInfo(BaseModel):
    """One disassembly line (ELF/Wasm/mpy)."""

    model_config = ConfigDict(extra="forbid")

    addr: int = Field(ge=0)
    text: str
    raw_hex: str = ""


def _naked_pack_bytes(data: bytes) -> bytes:
    try:
        return unwrap_mpzl(data)
    except ValueError:
        return data


_CODE_SOURCE_SUFFIXES = (".py", ".pyi", ".c", ".h", ".cc", ".cpp", ".hpp", ".rs")


def _is_code_source_path(path: str) -> bool:
    """True for paths worth scanning for symbol defs (not README.md / docs/ / .wat)."""
    if not path or path.endswith(".mpy"):
        return False
    norm = path.replace("\\", "/").lstrip("./").lower()
    if norm.startswith("docs/") or "/docs/" in f"/{norm}":
        return False
    return norm.endswith(_CODE_SOURCE_SUFFIXES)


def _embedded_text_sources(data: bytes) -> dict[str, str]:
    """Best-effort path→text map from pack/source for location search."""
    out: dict[str, str] = {}
    try:
        info = inspect_artifact(data)
    except Exception:
        return out
    paths: list[str] = []
    if info.source is not None:
        paths.extend(f.path for f in info.source.files if _is_code_source_path(f.path))
    if info.pack is not None:
        for f in info.pack.files:
            # Same path filter as source (rejects docs/); kind alone is not enough.
            if _is_code_source_path(f.path):
                paths.append(f.path)
    seen: set[str] = set()
    for path in paths:
        if path in seen or path.endswith(".mpy"):
            continue
        seen.add(path)
        try:
            view = extract_embedded_file(data, path)
        except (FileNotFoundError, ValueError):
            continue
        if view.text is not None:
            out[path] = view.text
    return out


def pack_addr2line(data: bytes, addr: int) -> list[LocationInfo]:
    """Map address → locations (DWARF or enclosing symbol)."""
    mod = _wasmmod_inspect_mod()
    if mod is None:
        return []
    naked = _naked_pack_bytes(data)
    out: list[LocationInfo] = []
    for loc in mod.addr2line(naked, int(addr)):
        out.append(
            LocationInfo(path=loc.path, line=loc.line, role=str(loc.role or "dwarf"))
        )
    return out


def pack_locations(data: bytes, name: str) -> list[LocationInfo]:
    """Locations for a symbol name (DWARF/sym + optional embedded source hits)."""
    mod = _wasmmod_inspect_mod()
    if mod is None:
        return []
    naked = _naked_pack_bytes(data)
    sources = _embedded_text_sources(data)
    out: list[LocationInfo] = []
    for loc in mod.locations_for_symbol(
        naked, name, source_files=sources or None
    ):
        out.append(
            LocationInfo(path=loc.path, line=loc.line, role=str(loc.role or "dwarf"))
        )
    return out


def pack_disasm(
    data: bytes, section_index: int, offset: int = 0, limit: int = 64
) -> list[DisasmLineInfo]:
    """Disassemble a window of a container section."""
    mod = _wasmmod_inspect_mod()
    if mod is None:
        return []
    naked = _naked_pack_bytes(data)
    lim = max(0, min(int(limit), 4096))
    out: list[DisasmLineInfo] = []
    for line in mod.disasm(naked, int(section_index), int(offset), lim):
        raw = getattr(line, "raw", b"") or b""
        out.append(
            DisasmLineInfo(
                addr=int(line.addr),
                text=str(line.text),
                raw_hex=bytes(raw).hex(),
            )
        )
    return out


def pack_mpy_disasm(mpy_bytes: bytes, limit: int = 80) -> list[DisasmLineInfo]:
    """Disassemble embedded MicroPython .mpy bytecode."""
    mod = _wasmmod_inspect_mod()
    if mod is None:
        return []
    lim = max(0, min(int(limit), 4096))
    out: list[DisasmLineInfo] = []
    for line in mod.mpy_disasm(mpy_bytes, lim):
        raw = getattr(line, "raw", b"") or b""
        out.append(
            DisasmLineInfo(
                addr=int(line.addr),
                text=str(line.text),
                raw_hex=bytes(raw).hex(),
            )
        )
    return out


SECTION_RAW_LIMIT_CAP = 1 << 20


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


class ArtifactContentsSummary(BaseModel):
    """Compact per-artifact row inside package-level ``PackageContents``."""

    model_config = ConfigDict(extra="forbid")

    filename: str
    kind: ArtifactBinaryKind
    encoding: ArtifactEnvelope
    size: int = Field(ge=0)
    signed: bool = False
    aot_version: int | None = Field(default=None, ge=1)
    pack_file_count: int = Field(default=0, ge=0)
    source_file_count: int = Field(default=0, ge=0)
    error: str | None = None


class PackageContents(BaseModel):
    """Package-level inventory stored on ``PackageEntry.contents`` / index.json."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: int = Field(default=1, alias="schema", ge=1)
    name: str | None = None
    pkg_version: str | None = None
    pack_files: list[str] = Field(default_factory=list)
    source_files: list[str] = Field(default_factory=list)
    exports: list[str] = Field(default_factory=list)
    imports: list[ImportInfo] = Field(default_factory=list)
    deps: dict[str, str] = Field(default_factory=dict)
    tags: dict[str, str] = Field(default_factory=dict)
    signed: bool = False
    has_pack: bool = False
    has_source: bool = False
    has_deps: bool = False
    aot_version: int | None = Field(default=None, ge=1)
    artifacts: list[ArtifactContentsSummary] = Field(default_factory=list)


class EmbeddedFileView(BaseModel):
    """Decoded embedded file for UI source view."""

    model_config = ConfigDict(extra="forbid")

    path: str
    section: Literal["source", "pack"]
    kind: str | None = None
    size: int = Field(ge=0)
    text: str | None = None
    binary: bool = False
    encoding: str = "utf-8"


def unwrap_mpzl(data: bytes) -> bytes:
    """Return naked artifact bytes (passthrough if not MPZL)."""
    if len(data) >= 8 and data[:4] == MPZL_MAGIC:
        (raw_len,) = struct.unpack_from("<I", data, 4)
        raw = zlib.decompress(data[8:])
        if len(raw) != raw_len:
            raise ValueError(f"MPZL length mismatch: got {len(raw)} want {raw_len}")
        return raw
    return data


def wrap_mpzl(data: bytes, *, level: int = 9) -> bytes:
    """Whole-artifact MPZL envelope (``MPZL`` | u32le raw_len | zlib)."""
    if len(data) >= 8 and data[:4] == MPZL_MAGIC:
        return data
    if len(data) > 0xFFFFFFFF:
        raise ValueError("artifact too large for MPZL")
    z = zlib.compress(data, level)
    return MPZL_MAGIC + struct.pack("<I", len(data)) + z


_NAKED_ARTIFACT = re.compile(r"^.+\.(?:wasm|aot\d*|elf)$", re.IGNORECASE)


def ensure_zlib_artifacts(files: dict[str, bytes]) -> dict[str, bytes]:
    """Ensure MPZL ``.zlib`` twins exist for naked ``.wasm`` / ``.aotN`` / ``.elf``.

    Fetch prefers ``.wasm.zlib``; naked copies are kept so older clients and
    inspect URLs keep working. If only a ``.zlib`` was uploaded, leave as-is.
    """
    out: dict[str, bytes] = dict(files)
    for name, data in list(files.items()):
        if name.endswith(".zlib") or not _NAKED_ARTIFACT.match(name):
            continue
        zname = f"{name}.zlib"
        if zname not in out:
            out[zname] = wrap_mpzl(data)
    return out


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
SIG_AUTO = 255


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


def inspect_artifact(data: bytes, *, filename: str = "") -> ArtifactContents:
    """Inspect one artifact (unwraps MPZL when present). Never raises."""
    encoding = (
        ArtifactEnvelope.MPZL
        if filename.endswith(".zlib") or (len(data) >= 4 and data[:4] == MPZL_MAGIC)
        else ArtifactEnvelope.RAW
    )
    try:
        naked = unwrap_mpzl(data)
    except ValueError as exc:
        return ArtifactContents(filename=filename, encoding=encoding, size=len(data), error=str(exc))

    if len(naked) >= 4 and naked[:4] == b"\x00asm":
        kind = ArtifactBinaryKind.WASM
    elif len(naked) >= 4 and naked[:4] == b"\x00aot":
        kind = ArtifactBinaryKind.AOT
    elif len(naked) >= 4 and naked[:4] == b"\x7fELF":
        kind = ArtifactBinaryKind.ELF
    else:
        kind = ArtifactBinaryKind.UNKNOWN

    info = ArtifactContents(
        filename=filename,
        kind=kind,
        encoding=encoding,
        size=len(data),
        naked_size=len(naked),
        aot_version=aot_version_from_filename(filename),
    )
    if kind is ArtifactBinaryKind.UNKNOWN:
        return info

    try:
        info.sections = list_container_sections(naked)
    except (ValueError, struct.error):
        info.sections = []

    try:
        info.symbols = list_pack_symbols(naked)
        info.has_dwarf = pack_has_dwarf(naked)
    except Exception:
        info.symbols = []
        info.has_dwarf = False

    try:
        info.signed = has_section(naked, SIG_SECTION)
    except (ValueError, struct.error):
        info.signed = False

    pack_raw = extract_custom_section(naked, PACK_SECTION)
    if pack_raw is not None:
        try:
            pack = parse_pack_payload(pack_raw)
            info.pack = enrich_pack_export_typesigs(pack, naked)
        except ValueError as exc:
            info.pack_error = str(exc)

    src_raw = extract_custom_section(naked, SOURCE_SECTION)
    if src_raw is not None:
        try:
            info.source = parse_source_payload(src_raw)
        except ValueError as exc:
            info.source_error = str(exc)

    imp_raw = extract_custom_section(naked, IMPORTS_SECTION)
    if imp_raw is not None:
        try:
            info.imports = parse_imports_payload(imp_raw)
        except ValueError as exc:
            info.imports_error = str(exc)

    deps_raw = extract_custom_section(naked, DEPS_SECTION)
    if deps_raw is not None:
        try:
            info.deps = parse_deps_payload(deps_raw)
        except ValueError as exc:
            info.deps_error = str(exc)

    sig_raw = extract_custom_section(naked, SIG_SECTION)
    if sig_raw is not None:
        try:
            info.sig = parse_sig_payload(sig_raw, naked=naked)
            info.signed = True
        except ValueError as exc:
            info.sig_error = str(exc)
            info.signed = True

    return info


def _score(art: ArtifactContents) -> int:
    score = 0
    if art.pack is not None:
        score += 10
    if art.source is not None:
        score += 8
    if art.signed:
        score += 2
    if art.kind is ArtifactBinaryKind.WASM or art.kind is ArtifactBinaryKind.ELF:
        score += 1
    return score


def merge_contents(artifacts: list[ArtifactContents]) -> PackageContents:
    """Build package-level ``PackageContents`` from per-artifact inspect results."""
    if not artifacts:
        return PackageContents()

    primary = max(artifacts, key=_score)
    pack = primary.pack
    source = primary.source
    pack_files = [f.path for f in pack.files] if pack is not None else []
    source_files = [f.path for f in source.files] if source is not None else []
    exports = [ex.export or ex.func for ex in (pack.exports if pack is not None else []) if ex.export or ex.func]

    aot_version: int | None = None
    for art in artifacts:
        if art.aot_version is not None:
            aot_version = art.aot_version
            break

    summaries = [
        ArtifactContentsSummary(
            filename=a.filename,
            kind=a.kind,
            encoding=a.encoding,
            size=a.size,
            signed=a.signed,
            aot_version=a.aot_version,
            pack_file_count=len(a.pack.files) if a.pack is not None else 0,
            source_file_count=len(a.source.files) if a.source is not None else 0,
            error=a.error or a.pack_error or a.source_error or a.imports_error or a.deps_error or a.sig_error,
        )
        for a in artifacts
    ]

    deps_map = {d.name: d.version for d in primary.deps}

    return PackageContents(
        name=(source.name if source is not None else None) or (pack.name if pack is not None else None),
        pkg_version=source.pkg_version if source is not None else None,
        pack_files=pack_files,
        source_files=source_files,
        exports=exports,
        imports=list(primary.imports),
        deps=deps_map,
        tags=dict(source.tags) if source is not None else {},
        signed=any(a.signed for a in artifacts),
        has_pack=any(a.pack is not None for a in artifacts),
        has_source=any(a.source is not None for a in artifacts),
        has_deps=bool(deps_map),
        aot_version=aot_version,
        artifacts=summaries,
    )


def inspect_upload(files: dict[str, bytes]) -> PackageContents:
    """Inspect all uploaded filenames → typed package contents for index/DB."""
    arts: list[ArtifactContents] = []
    for name, data in sorted(files.items()):
        try:
            arts.append(inspect_artifact(data, filename=name))
        except Exception as exc:
            arts.append(ArtifactContents(filename=name, error=str(exc), size=len(data)))
    return merge_contents(arts)


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
