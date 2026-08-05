"""HTTP API routers."""

from __future__ import annotations

import hashlib
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi import status as http_status
from fastapi.responses import Response
from sqlalchemy import text

from pymergetic.metal.cdn import __version__
from pymergetic.metal.cdn.api.deps import (
    SESSION_USER_KEY,
    AclServiceDep,
    ApiKeyServiceDep,
    AuditServiceDep,
    AuthUserDep,
    CurrentUserDep,
    IndexServiceDep,
    OptionalUserDep,
    OrgServiceDep,
    PublishServiceDep,
    SettingsDep,
    ShellSessionServiceDep,
    StorageDep,
    TrustServiceDep,
    UserServiceDep,
    get_db,
)
from pymergetic.metal.cdn.api.extended import (
    admin_router,
    audit_router,
    index_router,
    ops_router,
    orgs_router,
    register_package_extensions,
)
from pymergetic.metal.cdn.api.sessions import sessions_router
from pymergetic.metal.cdn.db import Database
from pymergetic.metal.cdn.layout import ChannelLayout
from pymergetic.metal.cdn.middleware.csrf import ensure_csrf_token
from pymergetic.metal.cdn.models import (
    ApiKeyCreate,
    ApiKeyCreated,
    ApiKeyRead,
    ClaimResult,
    CsrfResponse,
    HealthResponse,
    LoginRequest,
    PackageAclCreate,
    PackageAclRead,
    PackageEntry,
    PackageOwnership,
    PackageRole,
    PackageSummary,
    PresignUploadItem,
    PresignUploadRequest,
    PresignUploadResponse,
    PromoteRequest,
    PublishRequest,
    PublishResult,
    ReadyResponse,
    StatusResponse,
    TokenRequest,
    TransferRequest,
    UserCreate,
    UserRead,
    YankRequest,
)
from pymergetic.metal.cdn.services.shell_sessions import SESSION_ANON_KEY
from pymergetic.metal.cdn_client.contents import (
    ArtifactContents,
    ContainerSectionInfo,
    DisasmLineInfo,
    EmbeddedFileView,
    LocationInfo,
    SymbolInfo,
    extract_container_section,
    extract_embedded_bytes,
    extract_embedded_file,
    inspect_artifact,
    list_container_sections,
    list_pack_symbols,
    pack_addr2line,
    pack_disasm,
    pack_locations,
    pack_mpy_disasm,
    slice_bytes,
)

api_router = APIRouter()
health_router = APIRouter(tags=["health"])
auth_router = APIRouter(prefix="/auth", tags=["auth"])
users_router = APIRouter(prefix="/users", tags=["users"])
acl_router = APIRouter(prefix="/acl", tags=["acl"])
packages_router = APIRouter(prefix="/packages", tags=["packages"])
publish_router = APIRouter(prefix="/publish", tags=["publish"])
artifacts_router = APIRouter(prefix="/artifacts", tags=["artifacts"])


@health_router.get("/health", response_model=HealthResponse)
async def health(settings: SettingsDep) -> HealthResponse:
    """Liveness — process is up (no dependency checks)."""
    return HealthResponse(
        status="ok",
        version=__version__,
        experimental=settings.experimental,
        experimental_message=settings.experimental_message if settings.experimental else None,
    )


@health_router.get("/status", response_model=StatusResponse)
async def deployment_status(settings: SettingsDep) -> StatusResponse:
    """Public deployment flags (experimental banner, version)."""
    return StatusResponse(
        version=__version__,
        experimental=settings.experimental,
        experimental_message=settings.experimental_message if settings.experimental else None,
    )


@health_router.get("/ready", response_model=ReadyResponse)
async def ready(
    settings: SettingsDep,
    storage: StorageDep,
    db: Annotated[Database, Depends(get_db)],
) -> ReadyResponse:
    """Readiness — database + storage usable."""
    db_status = "ok"
    storage_status = "ok"
    try:
        async with db.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:
        db_status = f"error: {exc}"
    try:
        probe = "__ready_probe__"
        await storage.put_bytes(probe, b"ok")
        await storage.delete(probe)
    except Exception as exc:
        storage_status = f"error: {exc}"
    ok = db_status == "ok" and storage_status == "ok"
    if not ok:
        raise HTTPException(
            status_code=503,
            detail={
                "status": "not_ready",
                "version": __version__,
                "database": db_status,
                "storage": storage_status,
            },
        )
    return ReadyResponse(
        status="ok",
        version=__version__,
        database=db_status,
        storage=storage_status,
        experimental=settings.experimental,
    )


@auth_router.get("/csrf", response_model=CsrfResponse)
async def csrf_token(request: Request) -> CsrfResponse:
    return CsrfResponse(csrf_token=ensure_csrf_token(request))


@auth_router.post("/register", response_model=UserRead, status_code=http_status.HTTP_201_CREATED)
async def register(
    body: UserCreate,
    users: UserServiceDep,
    settings: SettingsDep,
    actor: OptionalUserDep,
) -> UserRead:
    if not settings.registration_open and (actor is None or not actor.is_admin):
        raise HTTPException(status_code=403, detail="registration closed")
    try:
        is_first = (await users.count()) == 0
        return await users.create(body, is_admin=is_first)
    except ValueError as exc:
        raise HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@auth_router.post("/login", response_model=UserRead)
async def login(
    body: LoginRequest,
    request: Request,
    users: UserServiceDep,
    shells: ShellSessionServiceDep,
) -> UserRead:
    user = await users.authenticate(body.email, body.password)
    if user is None:
        raise HTTPException(status_code=401, detail="invalid credentials")
    raw_anon = request.session.get(SESSION_ANON_KEY)
    if raw_anon:
        try:
            await shells.claim_anon(UUID(str(raw_anon)), user.id)
        except ValueError:
            pass
    request.session[SESSION_USER_KEY] = str(user.id)
    ensure_csrf_token(request)
    return user


@auth_router.post("/token", response_model=ApiKeyCreated)
async def issue_token(
    body: TokenRequest,
    users: UserServiceDep,
    keys: ApiKeyServiceDep,
) -> ApiKeyCreated:
    """Password → API key (for headless CLI / CI)."""
    user = await users.authenticate(body.email, body.password)
    if user is None:
        raise HTTPException(status_code=401, detail="invalid credentials")
    return await keys.create(user.id, ApiKeyCreate(name=body.name))


@auth_router.post("/logout", status_code=http_status.HTTP_204_NO_CONTENT)
async def logout(request: Request) -> None:
    # Drop identity only; mint a fresh anon so post-logout hits do not
    # reattach to previously claimed shell sessions.
    request.session.pop(SESSION_USER_KEY, None)
    request.session[SESSION_ANON_KEY] = str(uuid4())
    ensure_csrf_token(request)


@auth_router.get("/me", response_model=UserRead)
async def me(user: CurrentUserDep) -> UserRead:
    return user


@auth_router.post("/api-keys", response_model=ApiKeyCreated, status_code=http_status.HTTP_201_CREATED)
async def create_api_key(
    body: ApiKeyCreate,
    user: CurrentUserDep,
    keys: ApiKeyServiceDep,
) -> ApiKeyCreated:
    return await keys.create(user.id, body)


@auth_router.get("/api-keys", response_model=list[ApiKeyRead])
async def list_api_keys(user: CurrentUserDep, keys: ApiKeyServiceDep) -> list[ApiKeyRead]:
    return await keys.list_for_user(user.id)


@auth_router.delete("/api-keys/{key_id}", status_code=http_status.HTTP_204_NO_CONTENT)
async def revoke_api_key(key_id: UUID, user: CurrentUserDep, keys: ApiKeyServiceDep) -> None:
    ok = await keys.revoke(user.id, key_id)
    if not ok:
        raise HTTPException(status_code=404, detail="api key not found")


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


@packages_router.get("", response_model=list[PackageSummary])
async def list_packages(
    indexes: IndexServiceDep,
    acl: AclServiceDep,
    user: OptionalUserDep,
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
    indexes: IndexServiceDep,
    acl: AclServiceDep,
    user: OptionalUserDep,
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
    if entry is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="package not found")
    return entry


@publish_router.post("/presign", response_model=PresignUploadResponse)
async def publish_presign(
    body: PresignUploadRequest,
    storage: StorageDep,
    acl: AclServiceDep,
    settings: SettingsDep,
    actor: AuthUserDep,
) -> PresignUploadResponse:
    if settings.require_auth and actor is None:
        raise HTTPException(status_code=401, detail="authentication required")
    try:
        ChannelLayout.validate_package_name(body.package)
        ChannelLayout.pin(body.version)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if (
        actor is not None
        and not await acl.is_unclaimed(body.package)
        and not await acl.can_publish(body.package, actor.id)
        and not actor.is_admin
    ):
        raise HTTPException(status_code=403, detail="publisher lacks ACL for this package")
    channels = []
    if body.pin:
        channels.append(ChannelLayout.pin(body.version))
    if body.lead:
        channels.append(ChannelLayout.lead())
    if not channels:
        raise HTTPException(status_code=400, detail="at least one of pin/lead must be true")
    uploads: list[PresignUploadItem] = []
    for filename in body.filenames:
        for channel in channels:
            try:
                key = channel.artifact_key(filename)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            try:
                url = await storage.presign_put(key, expires_in=settings.s3_presign_expires_s)
            except NotImplementedError as exc:
                raise HTTPException(status_code=501, detail=str(exc)) from exc
            uploads.append(PresignUploadItem(filename=filename, key=key, url=url))
    return PresignUploadResponse(uploads=uploads, expires_in=settings.s3_presign_expires_s)


@publish_router.post("", response_model=PublishResult, status_code=http_status.HTTP_201_CREATED)
async def publish_pack(
    publish: PublishServiceDep,
    acl: AclServiceDep,
    orgs: OrgServiceDep,
    audit: AuditServiceDep,
    trust: TrustServiceDep,
    settings: SettingsDep,
    actor: AuthUserDep,
    meta: Annotated[str, Form(description="JSON PublishRequest")],
    files: Annotated[list[UploadFile], File()],
) -> PublishResult:
    try:
        request = PublishRequest.model_validate_json(meta)
    except Exception as exc:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    publisher_id = actor.id if actor is not None else request.publisher_user_id
    if settings.require_auth and publisher_id is None:
        raise HTTPException(status_code=401, detail="authentication required")

    if publisher_id is not None:
        if await acl.is_unclaimed(request.package):
            if settings.auto_claim_on_publish or actor is not None:
                try:
                    await acl.claim(request.package, publisher_id)
                except PermissionError as exc:
                    raise HTTPException(status_code=409, detail=str(exc)) from exc
            else:
                raise HTTPException(status_code=403, detail="claim package first")
        elif not await acl.can_publish(request.package, publisher_id):
            raise HTTPException(
                status_code=http_status.HTTP_403_FORBIDDEN,
                detail="publisher lacks ACL for this package",
            )

    blob_map: dict[str, bytes] = {}
    for upload in files:
        if not upload.filename:
            raise HTTPException(status_code=400, detail="file missing filename")
        blob_map[upload.filename] = await upload.read()

    if request.maintainer_email is None and actor is not None:
        request = request.model_copy(update={"maintainer_email": actor.email})

    try:
        roots = await trust.all_der() if settings.require_signed != "off" else []
        result = await publish.publish(request, blob_map, trust_roots=roots)
    except ValueError as exc:
        msg = str(exc)
        code = (
            http_status.HTTP_400_BAD_REQUEST
            if "signature" in msg.lower() or "wasmmod.sig" in msg.lower()
            else http_status.HTTP_409_CONFLICT
        )
        raise HTTPException(status_code=code, detail=msg) from exc
    await orgs.set_visibility(request.package, request.visibility)
    if actor is not None:
        await audit.record(
            "publish",
            actor_id=actor.id,
            package_name=request.package,
            detail=request.version,
        )
    return result


def _artifact_response(
    data: bytes,
    *,
    request: Request,
    cache_s: int,
    immutable: bool,
) -> Response:
    digest = hashlib.sha256(data).hexdigest()
    etag = f'"{digest}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    cache = f"public, max-age={cache_s}"
    if immutable:
        cache += ", immutable"
    headers = {
        "ETag": etag,
        "Cache-Control": cache,
        "Content-Length": str(len(data)),
    }
    # Browser wasmmod probes with HEAD (falls back to GET on 405); answer both.
    if request.method.upper() == "HEAD":
        return Response(status_code=200, media_type="application/octet-stream", headers=headers)
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers=headers,
    )


async def _load_artifact_bytes(storage: StorageDep, *, channel: str, filename: str) -> bytes:
    try:
        if channel == "lead":
            key = ChannelLayout.lead().artifact_key(filename)
        elif channel.startswith("@"):
            key = ChannelLayout.pin(channel[1:]).artifact_key(filename)
        else:
            key = ChannelLayout.pin(channel).artifact_key(filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not await storage.exists(key):
        raise HTTPException(status_code=404, detail="artifact not found")
    return await storage.get_bytes(key)


@artifacts_router.api_route("/lead/{filename}", methods=["GET", "HEAD"])
async def get_artifact_lead(
    filename: str,
    request: Request,
    storage: StorageDep,
    settings: SettingsDep,
) -> Response:
    data = await _load_artifact_bytes(storage, channel="lead", filename=filename)
    return _artifact_response(
        data,
        request=request,
        cache_s=settings.artifact_cache_lead_s,
        immutable=False,
    )


@artifacts_router.api_route("/pin/{version}/{filename}", methods=["GET", "HEAD"])
async def get_artifact_pinned(
    version: str,
    filename: str,
    request: Request,
    storage: StorageDep,
    settings: SettingsDep,
) -> Response:
    data = await _load_artifact_bytes(storage, channel=f"@{version}", filename=filename)
    return _artifact_response(
        data,
        request=request,
        cache_s=settings.artifact_cache_pin_s,
        # Storage pins are write-once; HTTP must NOT be immutable — force-republish
        # (e.g. sign-after-seed) keeps the same URL with a new body/ETag.
        immutable=False,
    )


@artifacts_router.get("/lead/{filename}/inspect", response_model=ArtifactContents)
async def inspect_artifact_lead(filename: str, storage: StorageDep) -> ArtifactContents:
    data = await _load_artifact_bytes(storage, channel="lead", filename=filename)
    return inspect_artifact(data, filename=filename)


@artifacts_router.get("/pin/{version}/{filename}/inspect", response_model=ArtifactContents)
async def inspect_artifact_pin(
    version: str, filename: str, storage: StorageDep
) -> ArtifactContents:
    data = await _load_artifact_bytes(storage, channel=f"@{version}", filename=filename)
    return inspect_artifact(data, filename=filename)


@artifacts_router.get("/lead/{filename}/files", response_model=EmbeddedFileView)
async def embedded_file_lead(
    filename: str, path: str, storage: StorageDep
) -> EmbeddedFileView:
    data = await _load_artifact_bytes(storage, channel="lead", filename=filename)
    try:
        return extract_embedded_file(data, path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"embedded path not found: {path}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@artifacts_router.get("/pin/{version}/{filename}/files", response_model=EmbeddedFileView)
async def embedded_file_pin(
    version: str, filename: str, path: str, storage: StorageDep
) -> EmbeddedFileView:
    data = await _load_artifact_bytes(storage, channel=f"@{version}", filename=filename)
    try:
        return extract_embedded_file(data, path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"embedded path not found: {path}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _embedded_raw_response(view_data: bytes, *, path: str) -> Response:
    import mimetypes
    from urllib.parse import quote

    ctype, _ = mimetypes.guess_type(path)
    if not ctype:
        ctype = "application/octet-stream"
    name = path.rsplit("/", 1)[-1]
    return Response(
        content=view_data,
        media_type=ctype,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}",
            "Cache-Control": "private, max-age=60",
        },
    )


@artifacts_router.get("/lead/{filename}/files/raw")
async def embedded_file_raw_lead(filename: str, path: str, storage: StorageDep) -> Response:
    data = await _load_artifact_bytes(storage, channel="lead", filename=filename)
    try:
        body, _section, _kind, _resolved = extract_embedded_bytes(data, path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"embedded path not found: {path}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _embedded_raw_response(body, path=path)


@artifacts_router.get("/pin/{version}/{filename}/files/raw")
async def embedded_file_raw_pin(
    version: str, filename: str, path: str, storage: StorageDep
) -> Response:
    data = await _load_artifact_bytes(storage, channel=f"@{version}", filename=filename)
    try:
        body, _section, _kind, _resolved = extract_embedded_bytes(data, path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"embedded path not found: {path}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _embedded_raw_response(body, path=path)


@artifacts_router.get("/lead/{filename}/symbols", response_model=list[SymbolInfo])
async def symbols_lead(filename: str, storage: StorageDep) -> list[SymbolInfo]:
    data = await _load_artifact_bytes(storage, channel="lead", filename=filename)
    return list_pack_symbols(data)


@artifacts_router.get("/pin/{version}/{filename}/symbols", response_model=list[SymbolInfo])
async def symbols_pin(
    version: str, filename: str, storage: StorageDep
) -> list[SymbolInfo]:
    data = await _load_artifact_bytes(storage, channel=f"@{version}", filename=filename)
    return list_pack_symbols(data)


@artifacts_router.get("/lead/{filename}/addr2line", response_model=list[LocationInfo])
async def addr2line_lead(
    filename: str, addr: int, storage: StorageDep
) -> list[LocationInfo]:
    """Map ``addr`` (decimal integer query) to source/symbol locations.

    Pass a decimal integer (e.g. ``?addr=16``). Hex ``0x`` prefixes are not
    required — clients should convert before calling.
    """
    data = await _load_artifact_bytes(storage, channel="lead", filename=filename)
    return pack_addr2line(data, addr)


@artifacts_router.get("/pin/{version}/{filename}/addr2line", response_model=list[LocationInfo])
async def addr2line_pin(
    version: str, filename: str, addr: int, storage: StorageDep
) -> list[LocationInfo]:
    """Pin variant of addr2line (``addr`` is a decimal integer query param)."""
    data = await _load_artifact_bytes(storage, channel=f"@{version}", filename=filename)
    return pack_addr2line(data, addr)


@artifacts_router.get("/lead/{filename}/locations", response_model=list[LocationInfo])
async def locations_lead(
    filename: str, name: str, storage: StorageDep
) -> list[LocationInfo]:
    data = await _load_artifact_bytes(storage, channel="lead", filename=filename)
    return pack_locations(data, name)


@artifacts_router.get("/pin/{version}/{filename}/locations", response_model=list[LocationInfo])
async def locations_pin(
    version: str, filename: str, name: str, storage: StorageDep
) -> list[LocationInfo]:
    data = await _load_artifact_bytes(storage, channel=f"@{version}", filename=filename)
    return pack_locations(data, name)


@artifacts_router.get("/lead/{filename}/disasm", response_model=list[DisasmLineInfo])
async def disasm_lead(
    filename: str,
    index: int,
    storage: StorageDep,
    offset: int = 0,
    limit: int = 64,
) -> list[DisasmLineInfo]:
    data = await _load_artifact_bytes(storage, channel="lead", filename=filename)
    return pack_disasm(data, index, offset=offset, limit=limit)


@artifacts_router.get("/pin/{version}/{filename}/disasm", response_model=list[DisasmLineInfo])
async def disasm_pin(
    version: str,
    filename: str,
    index: int,
    storage: StorageDep,
    offset: int = 0,
    limit: int = 64,
) -> list[DisasmLineInfo]:
    data = await _load_artifact_bytes(storage, channel=f"@{version}", filename=filename)
    return pack_disasm(data, index, offset=offset, limit=limit)


@artifacts_router.get(
    "/lead/{filename}/files/mpy-disasm", response_model=list[DisasmLineInfo]
)
async def mpy_disasm_lead(
    filename: str, path: str, storage: StorageDep, limit: int = 80
) -> list[DisasmLineInfo]:
    data = await _load_artifact_bytes(storage, channel="lead", filename=filename)
    try:
        body, _section, _kind, _resolved = extract_embedded_bytes(data, path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"embedded path not found: {path}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return pack_mpy_disasm(body, limit=limit)


@artifacts_router.get(
    "/pin/{version}/{filename}/files/mpy-disasm", response_model=list[DisasmLineInfo]
)
async def mpy_disasm_pin(
    version: str, filename: str, path: str, storage: StorageDep, limit: int = 80
) -> list[DisasmLineInfo]:
    data = await _load_artifact_bytes(storage, channel=f"@{version}", filename=filename)
    try:
        body, _section, _kind, _resolved = extract_embedded_bytes(data, path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"embedded path not found: {path}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return pack_mpy_disasm(body, limit=limit)


@artifacts_router.get("/lead/{filename}/sections", response_model=list[ContainerSectionInfo])
async def sections_lead(filename: str, storage: StorageDep) -> list[ContainerSectionInfo]:
    data = await _load_artifact_bytes(storage, channel="lead", filename=filename)
    return list_container_sections(data)


@artifacts_router.get("/pin/{version}/{filename}/sections", response_model=list[ContainerSectionInfo])
async def sections_pin(
    version: str, filename: str, storage: StorageDep
) -> list[ContainerSectionInfo]:
    data = await _load_artifact_bytes(storage, channel=f"@{version}", filename=filename)
    return list_container_sections(data)


def _section_raw_response(
    body: bytes, *, index: int, name: str, offset: int = 0
) -> Response:
    from urllib.parse import quote

    safe = name.replace("/", "_") or f"section_{index}"
    return Response(
        content=body,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(safe)}",
            "Cache-Control": "private, max-age=60",
            "X-Section-Index": str(index),
            "X-Section-Name": name,
            "X-Section-Offset": str(offset),
            "X-Section-Length": str(len(body)),
        },
    )


@artifacts_router.get("/lead/{filename}/sections/raw")
async def section_raw_lead(
    filename: str,
    index: int,
    storage: StorageDep,
    offset: int = 0,
    limit: int | None = None,
) -> Response:
    data = await _load_artifact_bytes(storage, channel="lead", filename=filename)
    try:
        body = extract_container_section(data, index=index)
        sections = list_container_sections(data)
        name = next((s.name for s in sections if s.index == index), f"section_{index}")
        body = slice_bytes(body, offset=offset, limit=limit)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _section_raw_response(body, index=index, name=name, offset=offset)


@artifacts_router.get("/pin/{version}/{filename}/sections/raw")
async def section_raw_pin(
    version: str,
    filename: str,
    index: int,
    storage: StorageDep,
    offset: int = 0,
    limit: int | None = None,
) -> Response:
    data = await _load_artifact_bytes(storage, channel=f"@{version}", filename=filename)
    try:
        body = extract_container_section(data, index=index)
        sections = list_container_sections(data)
        name = next((s.name for s in sections if s.index == index), f"section_{index}")
        body = slice_bytes(body, offset=offset, limit=limit)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _section_raw_response(body, index=index, name=name, offset=offset)


def build_api_router() -> APIRouter:
    api_router.include_router(health_router)
    api_router.include_router(ops_router)
    api_router.include_router(auth_router)
    api_router.include_router(users_router)
    api_router.include_router(acl_router)
    api_router.include_router(orgs_router)
    api_router.include_router(audit_router)
    api_router.include_router(index_router)
    api_router.include_router(admin_router)
    api_router.include_router(packages_router)
    api_router.include_router(publish_router)
    api_router.include_router(artifacts_router)
    api_router.include_router(sessions_router)
    return api_router
