"""Package list / search / claim / lifecycle + federation get."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi import status as http_status

from pymergetic.metal.cdn.api.deps import (
    AclServiceDep,
    AuditServiceDep,
    CurrentUserDep,
    IndexServiceDep,
    OptionalUserDep,
    PublishServiceDep,
    UserServiceDep,
)
from pymergetic.metal.cdn.api.extended import register_package_extensions
from pymergetic.metal.cdn.api.fed_deps import FederationProxyDep, FederationRegistryDep
from pymergetic.metal.cdn.layout import ChannelLayout
from pymergetic.metal.cdn.models import (
    ClaimResult,
    PackageAclRead,
    PackageEntry,
    PackageOwnership,
    PackageRole,
    PackageSummary,
    PromoteRequest,
    PublishResult,
    TransferRequest,
    YankRequest,
)
from pymergetic.metal.cdn.services.federation.forward import forward_json, resolve_mount

packages_router = APIRouter(prefix="/packages", tags=["packages"])


@packages_router.get("", response_model=list[PackageSummary])
async def list_packages(
    request: Request,
    indexes: IndexServiceDep,
    acl: AclServiceDep,
    user: OptionalUserDep,
    reg: FederationRegistryDep,
    proxy: FederationProxyDep,
    channel: str = "lead",
    include_yanked: bool = True,
) -> list[PackageSummary]:
    try:
        ref = ChannelLayout.lead() if channel == "lead" else ChannelLayout.pin(channel)
    except ValueError as exc:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    rows = await indexes.list_packages(ref, include_yanked=include_yanked)
    out: list[PackageSummary] = []
    for row in rows:
        ok = await acl.can_read(
            row.name,
            user.id if user else None,
            is_admin=bool(user and user.is_admin),
        )
        if ok:
            out.append(row)
    if channel == "lead":
        try:
            from pymergetic.metal.cdn.services.federation.catalog import merge_catalog

            cache = getattr(request.app.state, "federation_catalog_cache", None)
            out = await merge_catalog(out, reg=reg, proxy=proxy, request=request, cache=cache)
        except Exception:
            pass
    return out


@packages_router.get("/search", response_model=list[PackageSummary])
async def search_packages(
    indexes: IndexServiceDep,
    acl: AclServiceDep,
    user: OptionalUserDep,
    q: str = "",
    channel: str | None = None,
    include_yanked: bool = False,
) -> list[PackageSummary]:
    ref = None
    if channel is not None:
        try:
            ref = ChannelLayout.lead() if channel == "lead" else ChannelLayout.pin(channel)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    rows = await indexes.search(q, channel=ref, include_yanked=include_yanked)
    out: list[PackageSummary] = []
    for row in rows:
        ok = await acl.can_read(
            row.name,
            user.id if user else None,
            is_admin=bool(user and user.is_admin),
        )
        if ok:
            out.append(row)
    return out


@packages_router.get("/mine", response_model=list[PackageOwnership])
async def my_packages(user: CurrentUserDep, acl: AclServiceDep) -> list[PackageOwnership]:
    return await acl.list_for_user(user.id)


register_package_extensions(packages_router)


@packages_router.post("/{name:path}/claim", response_model=ClaimResult)
async def claim_package(
    name: str, user: CurrentUserDep, acl: AclServiceDep, audit: AuditServiceDep
) -> ClaimResult:
    try:
        ChannelLayout.validate_package_name(name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        result = await acl.claim(name, user.id)
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await audit.record("claim", actor_id=user.id, package_name=name, detail=str(result.created))
    return result


@packages_router.post("/{name:path}/promote", response_model=PublishResult)
async def promote_package(
    name: str,
    body: PromoteRequest,
    user: CurrentUserDep,
    acl: AclServiceDep,
    publish: PublishServiceDep,
    audit: AuditServiceDep,
) -> PublishResult:
    try:
        ChannelLayout.validate_package_name(name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not user.is_admin and not await acl.can_publish(name, user.id):
        raise HTTPException(status_code=403, detail="publisher lacks ACL for this package")
    try:
        result = await publish.promote(name, body.version)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await audit.record("promote", actor_id=user.id, package_name=name, detail=body.version)
    return result


@packages_router.post("/{name:path}/yank", response_model=PackageEntry)
async def yank_package(
    name: str,
    body: YankRequest,
    user: CurrentUserDep,
    acl: AclServiceDep,
    publish: PublishServiceDep,
    audit: AuditServiceDep,
) -> PackageEntry:
    try:
        ChannelLayout.validate_package_name(name)
        ref = (
            ChannelLayout.lead()
            if body.channel == "lead"
            else ChannelLayout.pin(body.channel.removeprefix("@"))
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not user.is_admin and not await acl.is_owner(name, user.id):
        raise HTTPException(status_code=403, detail="owner or admin required")
    try:
        entry = await publish.yank(name, channel=ref, reason=body.reason)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await audit.record("yank", actor_id=user.id, package_name=name, detail=body.reason)
    return entry


@packages_router.post("/{name:path}/transfer", response_model=PackageAclRead)
async def transfer_package(
    name: str,
    body: TransferRequest,
    user: CurrentUserDep,
    acl: AclServiceDep,
    users: UserServiceDep,
    audit: AuditServiceDep,
) -> PackageAclRead:
    try:
        ChannelLayout.validate_package_name(name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if await users.get(body.to_user_id) is None:
        raise HTTPException(status_code=404, detail="target user not found")
    try:
        await acl.transfer_owner(name, user.id, body.to_user_id)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    await audit.record("transfer", actor_id=user.id, package_name=name, detail=str(body.to_user_id))
    rows = await acl.list_for_package(name)
    for row in rows:
        if row.user_id == body.to_user_id and row.role == PackageRole.OWNER:
            return row
    raise HTTPException(status_code=500, detail="transfer failed")


@packages_router.get("/{name:path}", response_model=PackageEntry)
async def get_package(
    name: str,
    request: Request,
    indexes: IndexServiceDep,
    acl: AclServiceDep,
    user: OptionalUserDep,
    reg: FederationRegistryDep,
    proxy: FederationProxyDep,
    channel: str = "lead",
) -> PackageEntry:
    try:
        ChannelLayout.validate_package_name(name)
        ref = ChannelLayout.lead() if channel == "lead" else ChannelLayout.pin(channel)
    except ValueError as exc:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if not await acl.can_read(
        name, user.id if user else None, is_admin=bool(user and user.is_admin)
    ):
        raise HTTPException(status_code=404, detail="package not found")
    entry = await indexes.get_package(ref, name)
    if entry is not None:
        return entry
    mount = await resolve_mount(reg, name)
    if mount is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="package not found")
    data = await forward_json(
        proxy=proxy,
        reg=reg,
        mount=mount,
        path=f"/packages/{name}",
        request=request,
        params={"channel": channel},
    )
    return PackageEntry.model_validate(data)
