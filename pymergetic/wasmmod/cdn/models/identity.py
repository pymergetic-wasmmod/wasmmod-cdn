"""Auth / ACL / org / audit API schemas."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import EmailStr, Field
from sqlmodel import SQLModel

from pymergetic.wasmmod.cdn.models.tables import PackageRole, PackageVisibility


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


class PasswordChangeRequest(SQLModel):
    current_password: str = Field(min_length=8, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class ApiKeyCreate(SQLModel):
    name: str = Field(default="default", min_length=1, max_length=64)
    scopes: list[str] = Field(default_factory=list)


class ApiKeyCreated(SQLModel):
    id: UUID
    name: str
    prefix: str
    key: str
    scopes: list[str] = Field(default_factory=list)
    created_at: datetime


class ApiKeyRead(SQLModel):
    id: UUID
    name: str
    prefix: str
    scopes: list[str] = Field(default_factory=list)
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

