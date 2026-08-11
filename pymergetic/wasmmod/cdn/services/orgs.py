"""Organizations, teams, package visibility."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from pymergetic.wasmmod.cdn.models import (
    Organization,
    OrgCreate,
    OrgRead,
    PackageMeta,
    PackageMetaRead,
    PackageRole,
    PackageTeamAcl,
    PackageTeamAclCreate,
    PackageTeamAclRead,
    PackageVisibility,
    Team,
    TeamCreate,
    TeamMemberAdd,
    TeamMembership,
    TeamRead,
    utcnow,
)


class OrgService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_org(self, data: OrgCreate) -> OrgRead:
        row = Organization(slug=data.slug, display_name=data.display_name or data.slug)
        self._session.add(row)
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise ValueError(f"org slug taken: {data.slug}") from exc
        await self._session.refresh(row)
        return OrgRead.model_validate(row)

    async def get_org(self, org_id: UUID) -> OrgRead | None:
        row = await self._session.get(Organization, org_id)
        return OrgRead.model_validate(row) if row else None

    async def get_org_by_slug(self, slug: str) -> OrgRead | None:
        result = await self._session.exec(select(Organization).where(Organization.slug == slug))
        row = result.first()
        return OrgRead.model_validate(row) if row else None

    async def create_team(self, org_id: UUID, data: TeamCreate) -> TeamRead:
        if await self.get_org(org_id) is None:
            raise ValueError("org not found")
        row = Team(org_id=org_id, slug=data.slug, display_name=data.display_name or data.slug)
        self._session.add(row)
        await self._session.commit()
        await self._session.refresh(row)
        return TeamRead.model_validate(row)

    async def add_member(self, team_id: UUID, data: TeamMemberAdd) -> None:
        row = TeamMembership(team_id=team_id, user_id=data.user_id, role=data.role)
        self._session.add(row)
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise ValueError("already a team member") from exc

    async def grant_team(self, data: PackageTeamAclCreate) -> PackageTeamAclRead:
        row = PackageTeamAcl(
            package_name=data.package_name,
            team_id=data.team_id,
            role=data.role,
        )
        self._session.add(row)
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise ValueError("team ACL already exists") from exc
        await self._session.refresh(row)
        return PackageTeamAclRead.model_validate(row)

    async def team_role_for_user(self, package_name: str, user_id: UUID) -> PackageRole | None:
        grants = await self._session.exec(
            select(PackageTeamAcl).where(PackageTeamAcl.package_name == package_name)
        )
        best: PackageRole | None = None
        for grant in grants.all():
            mem = await self._session.exec(
                select(TeamMembership).where(
                    TeamMembership.team_id == grant.team_id,
                    TeamMembership.user_id == user_id,
                )
            )
            membership = mem.first()
            if membership is None:
                continue
            role = (
                PackageRole.OWNER
                if grant.role == PackageRole.OWNER or membership.role == PackageRole.OWNER
                else PackageRole.MAINTAINER
            )
            if best is None or role == PackageRole.OWNER:
                best = role
        return best

    async def get_meta(self, package_name: str) -> PackageMetaRead:
        row = await self._session.get(PackageMeta, package_name)
        if row is None:
            return PackageMetaRead(
                package_name=package_name,
                visibility=PackageVisibility.PUBLIC,
                org_id=None,
            )
        return PackageMetaRead.model_validate(row)

    async def set_visibility(
        self,
        package_name: str,
        visibility: PackageVisibility,
        *,
        org_id: UUID | None = None,
    ) -> PackageMetaRead:
        row = await self._session.get(PackageMeta, package_name)
        if row is None:
            row = PackageMeta(package_name=package_name, visibility=visibility, org_id=org_id)
        else:
            row.visibility = visibility
            row.updated_at = utcnow()
            if org_id is not None:
                row.org_id = org_id
        self._session.add(row)
        await self._session.commit()
        await self._session.refresh(row)
        return PackageMetaRead.model_validate(row)

    async def is_private(self, package_name: str) -> bool:
        meta = await self.get_meta(package_name)
        return meta.visibility == PackageVisibility.PRIVATE
