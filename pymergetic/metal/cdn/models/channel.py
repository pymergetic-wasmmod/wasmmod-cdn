"""Channel index / publish / catalog / health API schemas."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from pymergetic.metal.cdn.models.tables import PackageVisibility
from pymergetic.metal.cdn_client.contents import PackageContents


class SuccessorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    successor: str = Field(min_length=1, max_length=160)
    channel: str = "lead"
    deprecated: bool = True


class ClosureItem(BaseModel):
    name: str
    version: str


class ClosureResponse(BaseModel):
    root: str
    order: list[ClosureItem]


class PresignUploadRequest(BaseModel):
    package: str
    version: str
    filenames: list[str] = Field(min_length=1)
    pin: bool = True
    lead: bool = True


class PresignUploadItem(BaseModel):
    filename: str
    key: str
    url: str
    method: str = "PUT"


class PresignUploadResponse(BaseModel):
    uploads: list[PresignUploadItem]
    expires_in: int


class GcResult(BaseModel):
    dry_run: bool
    orphan_keys: list[str]
    deleted: list[str]


# ---------------------------------------------------------------------------
# Channel index (object store) — Pydantic only
# ---------------------------------------------------------------------------


class ArtifactKind(str, Enum):
    WASM = "wasm"
    AOT = "aot"
    ELF = "elf"


class ArtifactEncoding(str, Enum):
    RAW = "raw"
    MPZL = "mpzl"


class Artifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    kind: ArtifactKind
    encoding: ArtifactEncoding = ArtifactEncoding.RAW
    sha256: str = Field(min_length=64, max_length=64)
    size: int = Field(ge=0)
    arch: str | None = None
    aot_version: int | None = Field(default=None, ge=1)


class PackageEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = Field(min_length=1)
    aot_version: int | None = Field(default=None, ge=1)
    deps: dict[str, str] = Field(default_factory=dict)
    artifacts: list[Artifact] = Field(default_factory=list)
    maintainer_email: EmailStr | None = None
    description: str | None = Field(default=None, max_length=2000)
    homepage: str | None = Field(default=None, max_length=512)
    license: str | None = Field(default=None, max_length=128)
    yanked: bool = False
    yank_reason: str | None = Field(default=None, max_length=512)
    deprecated: bool = False
    successor: str | None = Field(
        default=None,
        max_length=160,
        description="Redirect target: package name or name@version",
    )
    contents: PackageContents | None = Field(
        default=None,
        description="Inventory extracted from uploaded .wasm/.aot/.elf (pack/source/sig)",
    )
    updated_at: datetime | None = Field(
        default=None,
        description="Last publish/overwrite time (UTC). Older indexes may omit this.",
    )


class ChannelIndex(BaseModel):
    """Static index.json beside artifacts under packs/ or packs/@version/."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: int = Field(default=1, alias="schema", ge=1)
    channel: str = Field(min_length=1)
    generated: datetime
    packages: dict[str, PackageEntry] = Field(default_factory=dict)
    signature: str | None = Field(
        default=None,
        description="Optional HMAC-SHA256 hex of canonical index body (excl. signature)",
    )

    @field_validator("channel")
    @classmethod
    def channel_ok(cls, value: str) -> str:
        if value != "lead" and not value.startswith("@"):
            raise ValueError("channel must be 'lead' or '@<version>'")
        return value


class PublishArtifactIn(BaseModel):
    """One file to place into a channel directory."""

    model_config = ConfigDict(extra="forbid")

    filename: str = Field(min_length=1)
    content_b64: str | None = None


class PublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    package: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=64)
    lead: bool = True
    pin: bool = True
    aot_version: int | None = Field(default=None, ge=1)
    deps: dict[str, str] = Field(default_factory=dict)
    maintainer_email: EmailStr | None = None
    description: str | None = Field(default=None, max_length=2000)
    homepage: str | None = Field(default=None, max_length=512)
    license: str | None = Field(default=None, max_length=128)
    force: bool = False
    visibility: PackageVisibility = PackageVisibility.PUBLIC
    # Deprecated: ignored when a session/API-key user is present.
    publisher_user_id: UUID | None = None


class PublishResult(BaseModel):
    package: str
    version: str
    channels: list[str]
    index_paths: list[str]
    artifacts: list[str]
    contents: PackageContents | None = None


class PromoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = Field(min_length=1, max_length=64)


class YankRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(default="yanked", min_length=1, max_length=512)
    channel: str = "lead"


class PackageSummary(BaseModel):
    name: str
    version: str
    channel: str
    artifact_count: int
    maintainer_email: EmailStr | None = None
    description: str | None = None
    yanked: bool = False
    deprecated: bool = False
    successor: str | None = None
    license: str | None = None
    homepage: str | None = None
    updated_at: datetime | None = None
    version_count: int = 1
    deps: dict[str, str] = Field(default_factory=dict)
    deps_ok: dict[str, bool] = Field(
        default_factory=dict,
        description="Per [deps] name: True if that exact version is published and not yanked.",
    )
    needed_by: list[str] = Field(
        default_factory=list,
        description="Published packages that list this name in [deps] (reverse deps).",
    )
    # Federation (local shadow wins; remotes tagged for UI).
    origin: str = Field(default="local", description="local | remote")
    mount_prefix: str | None = None
    peer_label: str | None = None
    peer_browse_url: str | None = None
    # Platform engine/kernel identity (from contents.tags.role or known names).
    role: str | None = Field(
        default=None,
        description="host | kernel when this is a platform pack; else None",
    )


class MaintainerSummary(BaseModel):
    """Package maintainer email with lead-channel package count."""

    email: EmailStr
    package_count: int


class ChannelSummary(BaseModel):
    name: str
    package_count: int
    is_lead: bool


class ChannelTreeNode(BaseModel):
    """Sidebar tree: one channel and its packages (legacy channel browse)."""

    channel: ChannelSummary
    packages: list[PackageSummary]


class PackageVersionOption(BaseModel):
    """One published channel/version for a package (lead or pin)."""

    channel: str
    version: str
    label: str
    artifact_count: int = 0


class PackageNavNode(BaseModel):
    """Collapsible package tree node.

    Path prefixes become folders (``children``). A published pack sets
    ``full_name`` + ``versions``. A package that is also a parent of nested
    packs (e.g. ``test_a`` with ``test_a.test_d``) has both.
    """

    name: str
    full_name: str | None = None
    children: list[PackageNavNode] = Field(default_factory=list)
    versions: list[PackageVersionOption] = Field(default_factory=list)
    origin: str = "local"
    peer_browse_url: str | None = None
    role: str | None = None

    @property
    def is_folder(self) -> bool:
        """True when this node has nested children (may also be a package)."""
        return bool(self.children)

    @property
    def is_package(self) -> bool:
        return self.full_name is not None

    @property
    def is_remote(self) -> bool:
        return self.origin == "remote"

    @property
    def is_platform(self) -> bool:
        return self.role in ("host", "kernel")


class HealthResponse(BaseModel):
    status: str
    version: str
    experimental: bool = False
    experimental_message: str | None = None


class StatusResponse(BaseModel):
    """Public deployment flags (no auth)."""

    version: str
    experimental: bool
    experimental_message: str | None = None


class ReadyResponse(BaseModel):
    status: str
    version: str
    database: str
    storage: str
    experimental: bool = False


class CsrfResponse(BaseModel):
    csrf_token: str
