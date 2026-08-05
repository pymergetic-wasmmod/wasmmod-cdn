"""Publish + presign upload endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile
from fastapi import status as http_status

from pymergetic.metal.cdn.api.deps import (
    AclServiceDep,
    AuditServiceDep,
    AuthUserDep,
    OrgServiceDep,
    PublishServiceDep,
    SessionDep,
    SettingsDep,
    StorageDep,
    TrustServiceDep,
)
from pymergetic.metal.cdn.api.fed_deps import FederationProxyDep, FederationRegistryDep
from pymergetic.metal.cdn.layout import ChannelLayout
from pymergetic.metal.cdn.models import (
    PresignUploadItem,
    PresignUploadRequest,
    PresignUploadResponse,
    PublishRequest,
    PublishResult,
)
from pymergetic.metal.cdn.services.federation.grant_acl import federation_bot_may_publish
from pymergetic.metal.cdn.services.federation.publish_upstream import (
    forward_publish,
    push_origin_headers,
    resolve_push_mount,
)

publish_router = APIRouter(prefix="/publish", tags=["publish"])


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
    http_request: Request,
    response: Response,
    publish: PublishServiceDep,
    acl: AclServiceDep,
    orgs: OrgServiceDep,
    audit: AuditServiceDep,
    trust: TrustServiceDep,
    settings: SettingsDep,
    actor: AuthUserDep,
    session: SessionDep,
    reg: FederationRegistryDep,
    proxy: FederationProxyDep,
    meta: Annotated[str, Form(description="JSON PublishRequest")],
    files: Annotated[list[UploadFile], File()],
    upstream: Annotated[
        bool,
        Form(description="When true, publish to a PUSH federation mount instead of local"),
    ] = False,
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
                if not upstream:
                    try:
                        await acl.claim(request.package, publisher_id)
                    except PermissionError as exc:
                        raise HTTPException(status_code=409, detail=str(exc)) from exc
            else:
                raise HTTPException(status_code=403, detail="claim package first")
        elif not await acl.can_publish(
            request.package, publisher_id
        ) and not await federation_bot_may_publish(
            session,
            user_id=publisher_id,
            package_name=request.package,
        ):
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

    if upstream:
        mount = await resolve_push_mount(reg, request.package)
        if mount is None:
            raise HTTPException(
                status_code=404,
                detail="no PUSH federation mount for this package prefix",
            )
        result = await forward_publish(
            proxy=proxy,
            reg=reg,
            mount=mount,
            request=http_request,
            meta=request,
            blob_map=blob_map,
        )
        for k, v in push_origin_headers(mount).items():
            response.headers[k] = v
        if actor is not None:
            await audit.record(
                "fed.publish.upstream",
                actor_id=actor.id,
                package_name=request.package,
                detail=f"{request.version} → {mount.prefix}",
            )
        return result

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
