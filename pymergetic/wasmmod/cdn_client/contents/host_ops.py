"""Optional host-side symbol / DWARF / disasm via ``pymergetic-wasmmod-tools``.

Requires the tools package on ``sys.path`` (editable install or PyPI). Soft-fails
to empty results when missing so the client wheel stays usable alone for inventory.
"""

from __future__ import annotations

from typing import Any

from pymergetic.wasmmod.cdn_client.contents.models import (
    DisasmLineInfo,
    LocationInfo,
    SymbolInfo,
)
from pymergetic.wasmmod.cdn_client.contents.mpzl import unwrap_mpzl


def _wasmmod_inspect_mod() -> Any | None:
    """Import shared inspect helpers from ``pymergetic-wasmmod-tools``."""
    try:
        from pymergetic.wasmmod.tools import inspect as wasmmod_inspect

        return wasmmod_inspect
    except ImportError:
        return None


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
    from pymergetic.wasmmod.cdn_client.contents.extract import extract_embedded_file
    from pymergetic.wasmmod.cdn_client.contents.inventory import inspect_artifact

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
