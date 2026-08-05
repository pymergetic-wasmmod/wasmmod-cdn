"""Users + package ACL endpoints."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException
from fastapi import status as http_status

from pymergetic.metal.cdn.api.deps import (
    AclServiceDep,
    AuthUserDep,
    CurrentUserDep,
    OptionalUserDep,
    SettingsDep,
    UserServiceDep,
)
from pymergetic.metal.cdn.layout import ChannelLayout
from pymergetic.metal.cdn.models import PackageAclCreate, PackageAclRead, UserCreate, UserRead

users_router = APIRouter(prefix="/users", tags=["users"])
acl_router = APIRouter(prefix="/acl", tags=["acl"])


@users_router.post("", response_model=UserRead, status_code=http_status.HTTP_201_CREATED)
async def create_user(
    body: UserCreate,
    users: UserServiceDep,
    settings: SettingsDep,
    actor: OptionalUserDep,
) -> UserRead:
    if settings.require_auth and (actor is None or not actor.is_admin):
        raise HTTPException(status_code=403, detail="admin required")
    try:
        return await users.create(body)
    except ValueError as exc:
        raise HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@users_router.get("", response_model=list[UserRead])
async def list_users(users: UserServiceDep, actor: AuthUserDep) -> list[UserRead]:
    if actor is not None and not actor.is_admin:
        raise HTTPException(status_code=403, detail="admin required")
    return await users.list_users()


@users_router.get("/{user_id}", response_model=UserRead)
async def get_user(user_id: UUID, users: UserServiceDep, actor: AuthUserDep) -> UserRead:
    if actor is not None and not actor.is_admin and actor.id != user_id:
        raise HTTPException(status_code=403, detail="forbidden")
    user = await users.get(user_id)
    if user is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="user not found")
    return user


@acl_router.post("", response_model=PackageAclRead, status_code=http_status.HTTP_201_CREATED)
async def grant_acl(
    body: PackageAclCreate,
    acl: AclServiceDep,
    actor: AuthUserDep,
) -> PackageAclRead:
    try:
        ChannelLayout.validate_package_name(body.package_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if (
        actor is not None
        and not actor.is_admin
        and not await acl.is_owner(body.package_name, actor.id)
    ):
        raise HTTPException(status_code=403, detail="owner or admin required")
    try:
        return await acl.grant(body)
    except ValueError as exc:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@acl_router.get("/{package_name}", response_model=list[PackageAclRead])
async def list_acl(package_name: str, acl: AclServiceDep) -> list[PackageAclRead]:
    return await acl.list_for_package(package_name)


@acl_router.delete("/{package_name}/{user_id}", status_code=http_status.HTTP_204_NO_CONTENT)
async def revoke_acl(
    package_name: str,
    user_id: UUID,
    acl: AclServiceDep,
    actor: CurrentUserDep,
) -> None:
    if not actor.is_admin and not await acl.is_owner(package_name, actor.id):
        raise HTTPException(status_code=403, detail="owner or admin required")
    ok = await acl.revoke(package_name, user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="acl not found")
