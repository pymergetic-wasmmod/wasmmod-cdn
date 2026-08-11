"""SQLModel table definitions (identity / ACL / org / audit)."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import ClassVar
from uuid import UUID, uuid4

from pydantic import EmailStr
from sqlalchemy import Column, DateTime, LargeBinary, UniqueConstraint
from sqlmodel import Field as SQLField
from sqlmodel import SQLModel

from pymergetic.wasmmod.cdn.models.common import utcnow


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
    # JSON list of scopes; empty = unrestricted (legacy keys).
    scopes: str = SQLField(default="", max_length=512)
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

