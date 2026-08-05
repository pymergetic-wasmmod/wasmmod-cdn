"""Pydantic inventory models for CDN index ``contents``."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


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
