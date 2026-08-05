"""SQLModel tables + Pydantic DTOs for CDN federation."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import ClassVar
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import Column, DateTime, UniqueConstraint
from sqlmodel import Field as SQLField
from sqlmodel import SQLModel

from pymergetic.metal.cdn.models import utcnow
from pymergetic.metal.cdn.services.federation.prefix import normalize_mount_prefix


class _OrmBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class FederationPeerStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    DISABLED = "disabled"


class FederationDirection(str, Enum):
    PULL = "pull"
    PUSH = "push"


class FederationShadowPolicy(str, Enum):
    LOCAL = "local"
    REMOTE = "remote"
    ERROR = "error"


class FederationCredKind(str, Enum):
    BEARER = "bearer"
    FED_KEY = "fed_key"


class FederationGrantStatus(str, Enum):
    ACTIVE = "active"
    REVOKED = "revoked"
    EXPIRED = "expired"


class FederationPeer(SQLModel, table=True):
    """Remote CDN identity (URL + label)."""

    __tablename__: ClassVar[str] = "federation_peers"

    id: UUID = SQLField(default_factory=uuid4, primary_key=True)
    label: str = SQLField(max_length=128, index=True)
    base_url: str = SQLField(max_length=512)
    public_browse_url: str | None = SQLField(default=None, max_length=512)
    status: FederationPeerStatus = SQLField(default=FederationPeerStatus.PENDING)
    created_by: UUID | None = SQLField(default=None, foreign_key="users.id")
    created_at: datetime = SQLField(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = SQLField(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class FederationMount(SQLModel, table=True):
    """Prefix mount pointing at a peer (parent side)."""

    __tablename__: ClassVar[str] = "federation_mounts"
    __table_args__ = (UniqueConstraint("prefix", name="uq_federation_mount_prefix"),)

    id: UUID = SQLField(default_factory=uuid4, primary_key=True)
    prefix: str = SQLField(max_length=128, index=True)
    peer_id: UUID = SQLField(foreign_key="federation_peers.id", index=True)
    direction: FederationDirection = SQLField(default=FederationDirection.PULL)
    shadow_policy: FederationShadowPolicy = SQLField(default=FederationShadowPolicy.LOCAL)
    max_hops_override: int | None = SQLField(default=None, ge=1, le=64)
    enabled: bool = SQLField(default=True)
    notes: str = SQLField(default="", max_length=500)
    created_at: datetime = SQLField(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = SQLField(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class FederationCredential(SQLModel, table=True):
    """Encrypted machine credential for parent→child calls."""

    __tablename__: ClassVar[str] = "federation_credentials"

    id: UUID = SQLField(default_factory=uuid4, primary_key=True)
    peer_id: UUID = SQLField(foreign_key="federation_peers.id", index=True)
    mount_id: UUID | None = SQLField(default=None, foreign_key="federation_mounts.id", index=True)
    kind: FederationCredKind = SQLField(default=FederationCredKind.BEARER)
    ciphertext: str = SQLField(max_length=4096)
    fingerprint: str = SQLField(default="", max_length=32)
    key_id: str = SQLField(default="", max_length=64)
    created_at: datetime = SQLField(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    rotated_at: datetime | None = SQLField(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )


class FederationGrant(SQLModel, table=True):
    """Child-side accept record: which parent may pull which prefix."""

    __tablename__: ClassVar[str] = "federation_grants"

    id: UUID = SQLField(default_factory=uuid4, primary_key=True)
    prefix: str = SQLField(max_length=128, index=True)
    parent_label: str = SQLField(max_length=128)
    parent_base_url: str | None = SQLField(default=None, max_length=512)
    parent_public_key: str | None = SQLField(default=None, max_length=2048)
    api_key_id: UUID | None = SQLField(default=None, foreign_key="api_keys.id")
    status: FederationGrantStatus = SQLField(default=FederationGrantStatus.ACTIVE)
    created_by: UUID | None = SQLField(default=None, foreign_key="users.id")
    created_at: datetime = SQLField(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    expires_at: datetime | None = SQLField(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    revoked_at: datetime | None = SQLField(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )


# --- DTOs -------------------------------------------------------------------


class FederationPeerCreate(_OrmBase):
    label: str = Field(min_length=1, max_length=128)
    base_url: str = Field(min_length=8, max_length=512)
    public_browse_url: str | None = Field(default=None, max_length=512)
    status: FederationPeerStatus = FederationPeerStatus.ACTIVE

    @field_validator("base_url", "public_browse_url")
    @classmethod
    def _url_ok(cls, value: str | None) -> str | None:
        if value is None:
            return None
        v = value.strip().rstrip("/")
        if not v:
            return None
        if not v.startswith(("http://", "https://")):
            raise ValueError("must be an http(s) URL")
        return v


class FederationPeerUpdate(_OrmBase):
    label: str | None = Field(default=None, min_length=1, max_length=128)
    base_url: str | None = None
    public_browse_url: str | None = None
    status: FederationPeerStatus | None = None

    @field_validator("base_url", "public_browse_url")
    @classmethod
    def _url_ok(cls, value: str | None) -> str | None:
        if value is None:
            return None
        v = value.strip().rstrip("/")
        if not v:
            return None
        if not v.startswith(("http://", "https://")):
            raise ValueError("must be an http(s) URL")
        return v


class FederationPeerRead(_OrmBase):
    id: UUID
    label: str
    base_url: str
    public_browse_url: str | None
    status: FederationPeerStatus
    created_by: UUID | None
    created_at: datetime
    updated_at: datetime


class FederationMountCreate(_OrmBase):
    prefix: str = Field(min_length=1, max_length=128)
    peer_id: UUID
    direction: FederationDirection = FederationDirection.PULL
    shadow_policy: FederationShadowPolicy = FederationShadowPolicy.LOCAL
    max_hops_override: int | None = Field(default=None, ge=1, le=64)
    enabled: bool = True
    notes: str = Field(default="", max_length=500)
    # Optional bearer set at create time (encrypted at rest).
    bearer_token: str | None = Field(default=None, min_length=8, max_length=512)

    @field_validator("prefix")
    @classmethod
    def _prefix_ok(cls, value: str) -> str:
        return normalize_mount_prefix(value)


class FederationMountUpdate(_OrmBase):
    peer_id: UUID | None = None
    direction: FederationDirection | None = None
    shadow_policy: FederationShadowPolicy | None = None
    max_hops_override: int | None = Field(default=None, ge=1, le=64)
    enabled: bool | None = None
    notes: str | None = Field(default=None, max_length=500)


class FederationMountRead(_OrmBase):
    id: UUID
    prefix: str
    peer_id: UUID
    direction: FederationDirection
    shadow_policy: FederationShadowPolicy
    max_hops_override: int | None
    enabled: bool
    notes: str
    created_at: datetime
    updated_at: datetime
    has_credential: bool = False
    credential_fingerprint: str | None = None
    # Joined for admin convenience
    peer_label: str | None = None
    peer_base_url: str | None = None


class FederationCredentialSet(_OrmBase):
    bearer_token: str = Field(min_length=8, max_length=512)
    key_id: str = Field(default="", max_length=64)


class FederationGrantAccept(_OrmBase):
    """Child: accept a parent link — mint scoped API key + grant row."""

    prefix: str = Field(min_length=1, max_length=128)
    parent_label: str = Field(min_length=1, max_length=128)
    parent_base_url: str | None = Field(default=None, max_length=512)
    key_name: str = Field(default="federation-parent", min_length=1, max_length=64)

    @field_validator("prefix")
    @classmethod
    def _prefix_ok(cls, value: str) -> str:
        return normalize_mount_prefix(value)

    @field_validator("parent_base_url")
    @classmethod
    def _url_ok(cls, value: str | None) -> str | None:
        if value is None:
            return None
        v = value.strip().rstrip("/")
        if not v:
            return None
        if not v.startswith(("http://", "https://")):
            raise ValueError("must be an http(s) URL")
        return v


class FederationGrantRead(_OrmBase):
    id: UUID
    prefix: str
    parent_label: str
    parent_base_url: str | None
    api_key_id: UUID | None
    status: FederationGrantStatus
    created_by: UUID | None
    created_at: datetime
    expires_at: datetime | None
    revoked_at: datetime | None


class FederationGrantAccepted(FederationGrantRead):
    """One-time reveal of the minted bearer for the parent to store."""

    api_key: str
    api_key_prefix: str


class FederationPublicMount(_OrmBase):
    prefix: str
    peer_label: str
    peer_browse_url: str
    direction: FederationDirection = FederationDirection.PULL


class FederationStatus(_OrmBase):
    peers: int
    mounts_enabled: int
    mounts_total: int
    grants_active: int
    max_hops: int
    proxy_ready: bool = False
    detail: str = "registry only — read proxy lands in a later phase"
