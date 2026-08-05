"""Org/team, audit, index fetch, closure, successor, presign, GC, metrics."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile, status

from pymergetic.metal.cdn.api.deps import (
    AclServiceDep,
    AdminUserDep,
    AuditServiceDep,
    CurrentUserDep,
    IndexServiceDep,
    OptionalUserDep,
    OrgServiceDep,
    PublishServiceDep,
    SettingsDep,
    StorageDep,
    TrustServiceDep,
)
from pymergetic.metal.cdn.api.fed_deps import FederationProxyDep, FederationRegistryDep
from pymergetic.metal.cdn.layout import ChannelLayout
from pymergetic.metal.cdn.middleware.logging import metrics_text
from pymergetic.metal.cdn.models import (
    AuditEventRead,
    ChannelIndex,
    ClosureItem,
    ClosureResponse,
    GcResult,
    OrgCreate,
    OrgRead,
    PackageEntry,
    PackageMetaRead,
    PackageTeamAclCreate,
    PackageTeamAclRead,
    PackageVersionOption,
    SuccessorRequest,
    TeamCreate,
    TeamMemberAdd,
    TeamRead,
    TrustRootRead,
    VisibilityUpdate,
)
from pymergetic.metal.cdn.resolve import resolve_install_order
from pymergetic.metal.cdn.services.federation.forward import forward_json, resolve_mount
from pymergetic.metal.cdn.storage import collect_orphan_keys

orgs_router = APIRouter(prefix="/orgs", tags=["orgs"])
audit_router = APIRouter(prefix="/audit", tags=["audit"])
index_router = APIRouter(prefix="/index", tags=["index"])
admin_router = APIRouter(prefix="/admin", tags=["admin"])
ops_router = APIRouter(tags=["ops"])


@orgs_router.post("", response_model=OrgRead, status_code=status.HTTP_201_CREATED)
async def create_org(body: OrgCreate, user: CurrentUserDep, orgs: OrgServiceDep) -> OrgRead:
    del user
    try:
        return await orgs.create_org(body)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@orgs_router.post("/{org_id}/teams", response_model=TeamRead, status_code=201)
async def create_team(
    org_id: UUID, body: TeamCreate, user: CurrentUserDep, orgs: OrgServiceDep
) -> TeamRead:
    del user
    try:
        return await orgs.create_team(org_id, body)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@orgs_router.post("/teams/{team_id}/members", status_code=204)
async def add_team_member(
    team_id: UUID, body: TeamMemberAdd, user: CurrentUserDep, orgs: OrgServiceDep
) -> None:
    del user
    try:
        await orgs.add_member(team_id, body)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@orgs_router.post("/team-acl", response_model=PackageTeamAclRead, status_code=201)
async def grant_team_acl(
    body: PackageTeamAclCreate,
    user: CurrentUserDep,
    orgs: OrgServiceDep,
    acl: AclServiceDep,
    audit: AuditServiceDep,
) -> PackageTeamAclRead:
    try:
        ChannelLayout.validate_package_name(body.package_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not user.is_admin and not await acl.is_owner(body.package_name, user.id):
        raise HTTPException(status_code=403, detail="owner or admin required")
    try:
        row = await orgs.grant_team(body)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await audit.record(
        "team_acl_grant",
        actor_id=user.id,
        package_name=body.package_name,
        detail=f"team={body.team_id} role={body.role}",
    )
    return row


@audit_router.get("", response_model=list[AuditEventRead])
async def list_audit(
    user: AdminUserDep,
    audit: AuditServiceDep,
    package: str | None = None,
    limit: int = 100,
) -> list[AuditEventRead]:
    del user
    return await audit.list_recent(package_name=package, limit=min(limit, 500))


@index_router.get("/lead", response_model=ChannelIndex)
async def get_lead_index(
    indexes: IndexServiceDep,
    storage: StorageDep,
    request: Request,
) -> Response:
    return await _index_response(indexes, storage, request, ChannelLayout.lead())


@index_router.get("/pin/{version}", response_model=ChannelIndex)
async def get_pin_index(
    version: str,
    indexes: IndexServiceDep,
    storage: StorageDep,
    request: Request,
) -> Response:
    try:
        ref = ChannelLayout.pin(version)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return await _index_response(indexes, storage, request, ref)


async def _index_response(indexes, storage, request, ref) -> Response:
    key = ref.index_key()
    if not await storage.exists(key):
        # Empty signed-capable index
        index = await indexes.load(ref)
        payload = index.model_dump_json(by_alias=True).encode("utf-8")
    else:
        payload = await storage.get_bytes(key)
    import hashlib

    etag = f'"{hashlib.sha256(payload).hexdigest()}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    return Response(
        content=payload,
        media_type="application/json",
        headers={"ETag": etag, "Cache-Control": "public, max-age=30"},
    )


@ops_router.get("/metrics")
async def prometheus_metrics(settings: SettingsDep) -> Response:
    if not settings.metrics_enabled:
        raise HTTPException(status_code=404, detail="metrics disabled")
    return Response(content=metrics_text(), media_type="text/plain; version=0.0.4")


@admin_router.post("/gc", response_model=GcResult)
async def run_gc(
    user: AdminUserDep,
    storage: StorageDep,
    dry_run: bool = True,
) -> GcResult:
    del user
    orphans = await collect_orphan_keys(storage)
    deleted: list[str] = []
    if not dry_run:
        for key in orphans:
            await storage.delete(key)
            deleted.append(key)
    return GcResult(dry_run=dry_run, orphan_keys=orphans, deleted=deleted)


@admin_router.get("/trust", response_model=list[TrustRootRead])
async def list_trust(user: AdminUserDep, trust: TrustServiceDep) -> list[TrustRootRead]:
    del user
    return await trust.list_roots()


@admin_router.post("/trust", response_model=TrustRootRead, status_code=status.HTTP_201_CREATED)
async def add_trust(
    user: AdminUserDep,
    trust: TrustServiceDep,
    file: UploadFile = File(...),
    name: str = Form(""),
) -> TrustRootRead:
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty certificate")
    try:
        return await trust.add(data, name=name or (file.filename or ""), created_by=user.id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"invalid CA: {exc}") from exc


@admin_router.delete("/trust/{root_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_trust(root_id: UUID, user: AdminUserDep, trust: TrustServiceDep) -> None:
    del user
    if not await trust.delete(root_id):
        raise HTTPException(status_code=404, detail="trust root not found")


def register_package_extensions(packages_router: APIRouter) -> None:
    """Attach versions / successor / visibility / closure onto the packages router."""

    @packages_router.get("/{name:path}/versions", response_model=list[PackageVersionOption])
    async def package_versions(
        name: str,
        request: Request,
        indexes: IndexServiceDep,
        acl: AclServiceDep,
        user: OptionalUserDep,
        reg: FederationRegistryDep,
        proxy: FederationProxyDep,
    ) -> list[PackageVersionOption]:
        """Lead + pin channels that publish ``name`` (inspect / catalog pickers)."""
        try:
            ChannelLayout.validate_package_name(name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not await acl.can_read(
            name, user.id if user else None, is_admin=bool(user and user.is_admin)
        ):
            raise HTTPException(status_code=404, detail="package not found")
        rows = await indexes.package_versions(name)
        if rows:
            return rows
        mount = await resolve_mount(reg, name)
        if mount is None:
            raise HTTPException(status_code=404, detail="package not found")
        data = await forward_json(
            proxy=proxy,
            reg=reg,
            mount=mount,
            path=f"/packages/{name}/versions",
            request=request,
        )
        return [PackageVersionOption.model_validate(x) for x in data]

    @packages_router.post("/{name:path}/successor", response_model=PackageEntry)
    async def set_successor(
        name: str,
        body: SuccessorRequest,
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
            entry = await publish.set_successor(
                name, channel=ref, successor=body.successor, deprecated=body.deprecated
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        await audit.record(
            "successor",
            actor_id=user.id,
            package_name=name,
            detail=body.successor,
        )
        return entry

    @packages_router.put("/{name:path}/visibility", response_model=PackageMetaRead)
    async def set_visibility(
        name: str,
        body: VisibilityUpdate,
        user: CurrentUserDep,
        acl: AclServiceDep,
        orgs: OrgServiceDep,
        audit: AuditServiceDep,
    ) -> PackageMetaRead:
        try:
            ChannelLayout.validate_package_name(name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not user.is_admin and not await acl.is_owner(name, user.id):
            raise HTTPException(status_code=403, detail="owner or admin required")
        meta = await orgs.set_visibility(name, body.visibility)
        await audit.record(
            "visibility",
            actor_id=user.id,
            package_name=name,
            detail=body.visibility.value,
        )
        return meta

    @packages_router.get("/{name:path}/closure", response_model=ClosureResponse)
    async def package_closure(
        name: str,
        indexes: IndexServiceDep,
        version: str | None = None,
    ) -> ClosureResponse:
        try:
            ChannelLayout.validate_package_name(name.split("@", 1)[0])
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        root = f"{name}@{version}" if version else name
        lead = await indexes.load(ChannelLayout.lead())
        pins: dict = {}
        try:
            for ref in await indexes.discover_channels():
                if not ref.is_lead and ref.pin_version:
                    pins[ref.pin_version] = await indexes.load(ref)
            order = resolve_install_order(root, lead=lead, pins=pins)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return ClosureResponse(
            root=root,
            order=[ClosureItem(name=n, version=v) for n, v in order],
        )
