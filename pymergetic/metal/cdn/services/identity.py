"""Identity services (users + package ACL + API keys) — SQLModel / async session."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from pymergetic.metal.cdn.models import (
    ApiKey,
    ApiKeyCreate,
    ApiKeyCreated,
    ApiKeyRead,
    ClaimResult,
    PackageAcl,
    PackageAclCreate,
    PackageAclRead,
    PackageOwnership,
    PackageRole,
    User,
    UserCreate,
    UserRead,
    utcnow,
)
from pymergetic.metal.cdn.security import (
    generate_api_key,
    hash_api_key,
    hash_password,
    verify_password,
)


class UserService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def count(self) -> int:
        result = await self._session.exec(select(User))
        return len(result.all())

    async def create(
        self,
        data: UserCreate,
        *,
        is_admin: bool = False,
    ) -> UserRead:
        user = User(
            email=data.email,
            display_name=data.display_name or data.email.split("@", 1)[0],
            password_hash=hash_password(data.password),
            is_admin=is_admin,
        )
        self._session.add(user)
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise ValueError(f"email already registered: {data.email}") from exc
        await self._session.refresh(user)
        return UserRead.model_validate(user)

    async def ensure_bootstrap_admin(self, email: str, password: str) -> UserRead | None:
        existing = await self.get_by_email(email)
        if existing is not None:
            return None
        return await self.create(
            UserCreate(email=email, display_name="admin", password=password),
            is_admin=True,
        )

    async def authenticate(self, email: str, password: str) -> UserRead | None:
        result = await self._session.exec(select(User).where(User.email == email))
        user = result.first()
        if user is None or not user.is_active or not user.password_hash:
            return None
        if not verify_password(password, user.password_hash):
            return None
        return UserRead.model_validate(user)

    async def get(self, user_id: UUID) -> UserRead | None:
        user = await self._session.get(User, user_id)
        return UserRead.model_validate(user) if user else None

    async def get_by_email(self, email: str) -> UserRead | None:
        result = await self._session.exec(select(User).where(User.email == email))
        user = result.first()
        return UserRead.model_validate(user) if user else None

    async def list_users(self) -> list[UserRead]:
        result = await self._session.exec(select(User).order_by(col(User.email)))
        return [UserRead.model_validate(u) for u in result.all()]


class ApiKeyService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, user_id: UUID, data: ApiKeyCreate) -> ApiKeyCreated:
        full, prefix, key_hash = generate_api_key()
        row = ApiKey(user_id=user_id, name=data.name, prefix=prefix, key_hash=key_hash)
        self._session.add(row)
        await self._session.commit()
        await self._session.refresh(row)
        return ApiKeyCreated(
            id=row.id,
            name=row.name,
            prefix=row.prefix,
            key=full,
            created_at=row.created_at,
        )

    async def list_for_user(self, user_id: UUID) -> list[ApiKeyRead]:
        result = await self._session.exec(
            select(ApiKey).where(ApiKey.user_id == user_id).order_by(col(ApiKey.created_at))
        )
        return [ApiKeyRead.model_validate(r) for r in result.all()]

    async def revoke(self, user_id: UUID, key_id: UUID) -> bool:
        row = await self._session.get(ApiKey, key_id)
        if row is None or row.user_id != user_id:
            return False
        if row.revoked_at is None:
            row.revoked_at = utcnow()
            self._session.add(row)
            await self._session.commit()
        return True

    async def resolve_user_id(self, full_key: str) -> UUID | None:
        digest = hash_api_key(full_key)
        result = await self._session.exec(select(ApiKey).where(ApiKey.key_hash == digest))
        row = result.first()
        if row is None or row.revoked_at is not None:
            return None
        return row.user_id


class AclService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def grant(self, data: PackageAclCreate) -> PackageAclRead:
        row = PackageAcl(
            package_name=data.package_name,
            user_id=data.user_id,
            role=data.role,
        )
        self._session.add(row)
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise ValueError("ACL already exists for this user/package") from exc
        await self._session.refresh(row)
        return PackageAclRead.model_validate(row)

    async def list_for_package(self, package_name: str) -> list[PackageAclRead]:
        result = await self._session.exec(
            select(PackageAcl).where(PackageAcl.package_name == package_name)
        )
        return [PackageAclRead.model_validate(r) for r in result.all()]

    async def list_for_user(self, user_id: UUID) -> list[PackageOwnership]:
        result = await self._session.exec(select(PackageAcl).where(PackageAcl.user_id == user_id))
        return [PackageOwnership(package_name=r.package_name, role=r.role) for r in result.all()]

    async def get_role(self, package_name: str, user_id: UUID) -> PackageRole | None:
        result = await self._session.exec(
            select(PackageAcl).where(
                PackageAcl.package_name == package_name,
                PackageAcl.user_id == user_id,
            )
        )
        row = result.first()
        direct = row.role if row else None
        if direct == PackageRole.OWNER:
            return direct
        # Team grants (lazy import to avoid cycles).
        from pymergetic.metal.cdn.services.orgs import OrgService

        team_role = await OrgService(self._session).team_role_for_user(package_name, user_id)
        if direct == PackageRole.MAINTAINER or team_role == PackageRole.MAINTAINER:
            if team_role == PackageRole.OWNER:
                return PackageRole.OWNER
            return PackageRole.MAINTAINER if direct or team_role else None
        return team_role or direct

    async def is_unclaimed(self, package_name: str) -> bool:
        result = await self._session.exec(
            select(PackageAcl).where(PackageAcl.package_name == package_name)
        )
        if result.first() is not None:
            return False
        from pymergetic.metal.cdn.models import PackageTeamAcl

        team = await self._session.exec(
            select(PackageTeamAcl).where(PackageTeamAcl.package_name == package_name)
        )
        return team.first() is None

    async def can_publish(self, package_name: str, user_id: UUID) -> bool:
        role = await self.get_role(package_name, user_id)
        return role in (PackageRole.OWNER, PackageRole.MAINTAINER)

    async def is_owner(self, package_name: str, user_id: UUID) -> bool:
        return await self.get_role(package_name, user_id) == PackageRole.OWNER

    async def can_read(
        self, package_name: str, user_id: UUID | None, *, is_admin: bool = False
    ) -> bool:
        """Public packages are readable; private require ACL/admin."""
        from pymergetic.metal.cdn.services.orgs import OrgService

        if not await OrgService(self._session).is_private(package_name):
            return True
        if is_admin:
            return True
        if user_id is None:
            return False
        return await self.can_publish(package_name, user_id)

    async def claim(self, package_name: str, user_id: UUID) -> ClaimResult:
        role = await self.get_role(package_name, user_id)
        if role is not None:
            return ClaimResult(package_name=package_name, role=role, created=False)
        if not await self.is_unclaimed(package_name):
            raise PermissionError("package already claimed")
        row = await self.grant(
            PackageAclCreate(
                package_name=package_name,
                user_id=user_id,
                role=PackageRole.OWNER,
            )
        )
        return ClaimResult(package_name=package_name, role=row.role, created=True)

    async def revoke(self, package_name: str, user_id: UUID) -> bool:
        result = await self._session.exec(
            select(PackageAcl).where(
                PackageAcl.package_name == package_name,
                PackageAcl.user_id == user_id,
            )
        )
        row = result.first()
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.commit()
        return True

    async def transfer_owner(self, package_name: str, from_user: UUID, to_user: UUID) -> None:
        if not await self.is_owner(package_name, from_user):
            raise PermissionError("only the owner can transfer")
        target = await self.get_role(package_name, to_user)
        if target is None:
            await self.grant(
                PackageAclCreate(
                    package_name=package_name,
                    user_id=to_user,
                    role=PackageRole.OWNER,
                )
            )
        else:
            result = await self._session.exec(
                select(PackageAcl).where(
                    PackageAcl.package_name == package_name,
                    PackageAcl.user_id == to_user,
                )
            )
            row = result.first()
            assert row is not None
            row.role = PackageRole.OWNER
            self._session.add(row)
            await self._session.commit()
        # Demote previous owner to maintainer (keeps history of access).
        result = await self._session.exec(
            select(PackageAcl).where(
                PackageAcl.package_name == package_name,
                PackageAcl.user_id == from_user,
            )
        )
        old = result.first()
        if old is not None:
            old.role = PackageRole.MAINTAINER
            self._session.add(old)
            await self._session.commit()
