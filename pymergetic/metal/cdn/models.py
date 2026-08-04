"""Domain / API models — SQLModel for identity, Pydantic for channel index."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import ClassVar
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator
from sqlalchemy import Column, DateTime, LargeBinary, UniqueConstraint
from sqlmodel import Field as SQLField
from sqlmodel import SQLModel

from pymergetic.metal.cdn_client.contents import PackageContents


def utcnow() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Identity (Postgres / SQLite) — separate from pack channel state
# ---------------------------------------------------------------------------


class User(SQLModel, table=True):
    """Publisher account (email is identity; pack index may mirror it publicly)."""

    __tablename__: ClassVar[str] = "users"

    id: UUID = SQLField(default_factory=uuid4, primary_key=True)
    email: EmailStr = SQLField(index=True, unique=True, max_length=320)
    display_name: str = SQLField(default="", max_length=128)
    password_hash: str | None = SQLField(default=None, max_length=128)
    is_admin: bool = SQLField(default=False)
    is_active: bool = SQLField(default=True)
    created_at: datetime = SQLField(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class TrustRoot(SQLModel, table=True):
    """Server-wide CA root for optional publish-time MPWS verification."""

    __tablename__: ClassVar[str] = "trust_roots"

    id: UUID = SQLField(default_factory=uuid4, primary_key=True)
    name: str = SQLField(default="", max_length=128)
    sha256: str = SQLField(index=True, unique=True, max_length=64)
    der: bytes = SQLField(sa_column=Column(LargeBinary, nullable=False))
    subject: str = SQLField(default="", max_length=512)
    created_by: UUID | None = SQLField(default=None, foreign_key="users.id")
    created_at: datetime = SQLField(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class TrustRootRead(SQLModel):
    id: UUID
    name: str
    sha256: str
    subject: str
    created_at: datetime


class TrustRootCreated(TrustRootRead):
    pass


class ApiKey(SQLModel, table=True):
    """CI / CLI bearer token (store hash only)."""

    __tablename__: ClassVar[str] = "api_keys"

    id: UUID = SQLField(default_factory=uuid4, primary_key=True)
    user_id: UUID = SQLField(foreign_key="users.id", index=True)
    name: str = SQLField(default="default", max_length=64)
    prefix: str = SQLField(index=True, max_length=16)
    key_hash: str = SQLField(unique=True, max_length=64)
    created_at: datetime = SQLField(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    revoked_at: datetime | None = SQLField(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )


class PackageRole(str, Enum):
    OWNER = "owner"
    MAINTAINER = "maintainer"


class PackageAcl(SQLModel, table=True):
    """Who may publish / promote a pack name (direct user grant)."""

    __tablename__: ClassVar[str] = "package_acl"
    __table_args__ = (UniqueConstraint("package_name", "user_id", name="uq_acl_pkg_user"),)

    id: UUID = SQLField(default_factory=uuid4, primary_key=True)
    package_name: str = SQLField(index=True, max_length=128)
    user_id: UUID = SQLField(foreign_key="users.id", index=True)
    role: PackageRole = SQLField(default=PackageRole.OWNER)
    created_at: datetime = SQLField(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class Organization(SQLModel, table=True):
    """Optional org that owns scoped package namespaces (``org/…``)."""

    __tablename__: ClassVar[str] = "organizations"

    id: UUID = SQLField(default_factory=uuid4, primary_key=True)
    slug: str = SQLField(index=True, unique=True, max_length=64)
    display_name: str = SQLField(default="", max_length=128)
    created_at: datetime = SQLField(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class Team(SQLModel, table=True):
    __tablename__: ClassVar[str] = "teams"

    id: UUID = SQLField(default_factory=uuid4, primary_key=True)
    org_id: UUID = SQLField(foreign_key="organizations.id", index=True)
    slug: str = SQLField(index=True, max_length=64)
    display_name: str = SQLField(default="", max_length=128)
    created_at: datetime = SQLField(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class TeamMembership(SQLModel, table=True):
    __tablename__: ClassVar[str] = "team_memberships"
    __table_args__ = (UniqueConstraint("team_id", "user_id", name="uq_team_user"),)

    id: UUID = SQLField(default_factory=uuid4, primary_key=True)
    team_id: UUID = SQLField(foreign_key="teams.id", index=True)
    user_id: UUID = SQLField(foreign_key="users.id", index=True)
    role: PackageRole = SQLField(default=PackageRole.MAINTAINER)
    created_at: datetime = SQLField(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class PackageTeamAcl(SQLModel, table=True):
    """Team grant on a package (resolved via team memberships)."""

    __tablename__: ClassVar[str] = "package_team_acl"
    __table_args__ = (UniqueConstraint("package_name", "team_id", name="uq_acl_pkg_team"),)

    id: UUID = SQLField(default_factory=uuid4, primary_key=True)
    package_name: str = SQLField(index=True, max_length=128)
    team_id: UUID = SQLField(foreign_key="teams.id", index=True)
    role: PackageRole = SQLField(default=PackageRole.MAINTAINER)
    created_at: datetime = SQLField(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class PackageVisibility(str, Enum):
    PUBLIC = "public"
    PRIVATE = "private"


class PackageMeta(SQLModel, table=True):
    """Identity-side package metadata (visibility / org), not channel index."""

    __tablename__: ClassVar[str] = "package_meta"

    package_name: str = SQLField(primary_key=True, max_length=128)
    visibility: PackageVisibility = SQLField(default=PackageVisibility.PUBLIC)
    org_id: UUID | None = SQLField(default=None, foreign_key="organizations.id", index=True)
    updated_at: datetime = SQLField(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class AuditEvent(SQLModel, table=True):
    """Append-only audit log for ACL / publish lifecycle actions."""

    __tablename__: ClassVar[str] = "audit_events"

    id: UUID = SQLField(default_factory=uuid4, primary_key=True)
    actor_id: UUID | None = SQLField(default=None, foreign_key="users.id", index=True)
    action: str = SQLField(index=True, max_length=64)
    package_name: str | None = SQLField(default=None, index=True, max_length=128)
    detail: str = SQLField(default="", max_length=2000)
    created_at: datetime = SQLField(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class UserCreate(SQLModel):
    email: EmailStr
    display_name: str = ""
    password: str = Field(min_length=8, max_length=128)


class UserRead(SQLModel):
    id: UUID
    email: EmailStr
    display_name: str
    is_admin: bool
    is_active: bool
    created_at: datetime


class LoginRequest(SQLModel):
    email: EmailStr
    password: str


class TokenRequest(SQLModel):
    email: EmailStr
    password: str
    name: str = Field(default="cli", min_length=1, max_length=64)


class ApiKeyCreate(SQLModel):
    name: str = Field(default="default", min_length=1, max_length=64)


class ApiKeyCreated(SQLModel):
    id: UUID
    name: str
    prefix: str
    key: str
    created_at: datetime


class ApiKeyRead(SQLModel):
    id: UUID
    name: str
    prefix: str
    created_at: datetime
    revoked_at: datetime | None


class PackageAclCreate(SQLModel):
    package_name: str = Field(min_length=1, max_length=128)
    user_id: UUID
    role: PackageRole = PackageRole.OWNER


class PackageAclRead(SQLModel):
    id: UUID
    package_name: str
    user_id: UUID
    role: PackageRole
    created_at: datetime


class ClaimResult(SQLModel):
    package_name: str
    role: PackageRole
    created: bool


class PackageOwnership(SQLModel):
    package_name: str
    role: PackageRole


class TransferRequest(SQLModel):
    to_user_id: UUID


class OrgCreate(SQLModel):
    slug: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    display_name: str = ""


class OrgRead(SQLModel):
    id: UUID
    slug: str
    display_name: str
    created_at: datetime


class TeamCreate(SQLModel):
    slug: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    display_name: str = ""


class TeamRead(SQLModel):
    id: UUID
    org_id: UUID
    slug: str
    display_name: str
    created_at: datetime


class TeamMemberAdd(SQLModel):
    user_id: UUID
    role: PackageRole = PackageRole.MAINTAINER


class PackageTeamAclCreate(SQLModel):
    package_name: str = Field(min_length=1, max_length=128)
    team_id: UUID
    role: PackageRole = PackageRole.MAINTAINER


class PackageTeamAclRead(SQLModel):
    id: UUID
    package_name: str
    team_id: UUID
    role: PackageRole
    created_at: datetime


class VisibilityUpdate(SQLModel):
    visibility: PackageVisibility


class PackageMetaRead(SQLModel):
    package_name: str
    visibility: PackageVisibility
    org_id: UUID | None


class AuditEventRead(SQLModel):
    id: UUID
    actor_id: UUID | None
    action: str
    package_name: str | None
    detail: str
    created_at: datetime


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
        description="Inventory extracted from uploaded .wasm/.aot (pack/source/sig)",
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
    children: list["PackageNavNode"] = Field(default_factory=list)
    versions: list[PackageVersionOption] = Field(default_factory=list)

    @property
    def is_folder(self) -> bool:
        """True when this node has nested children (may also be a package)."""
        return bool(self.children)

    @property
    def is_package(self) -> bool:
        return self.full_name is not None


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
