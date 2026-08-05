"""Per-artifact and package-level inventory."""

from __future__ import annotations

import struct

from pymergetic.metal.cdn_client.contents.const import (
    DEPS_SECTION,
    IMPORTS_SECTION,
    MPZL_MAGIC,
    PACK_SECTION,
    SIG_SECTION,
    SOURCE_SECTION,
)
from pymergetic.metal.cdn_client.contents.models import (
    ArtifactBinaryKind,
    ArtifactContents,
    ArtifactContentsSummary,
    ArtifactEnvelope,
    PackageContents,
)
from pymergetic.metal.cdn_client.contents.mpzl import unwrap_mpzl
from pymergetic.metal.cdn_client.contents.host_ops import list_pack_symbols, pack_has_dwarf
from pymergetic.metal.cdn_client.contents.parse import (
    aot_version_from_filename,
    parse_deps_payload,
    parse_imports_payload,
    parse_pack_payload,
    parse_sig_payload,
    parse_source_payload,
)
from pymergetic.metal.cdn_client.contents.sections import (
    enrich_pack_export_typesigs,
    extract_custom_section,
    has_section,
    list_container_sections,
)


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
