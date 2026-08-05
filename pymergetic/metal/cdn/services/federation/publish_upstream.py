"""Forward multipart publish to a PUSH mount peer."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request

from pymergetic.metal.cdn.models import PublishRequest, PublishResult
from pymergetic.metal.cdn.services.federation.forward import (
    authorization_for_mount,
    parse_incoming_hop,
    parse_trace,
    remote_headers,
)
from pymergetic.metal.cdn.services.federation.proxy import (
    FederationProxy,
    FederationProxyError,
)
from pymergetic.metal.cdn.services.federation.registry import FederationRegistry
from pymergetic.metal.cdn.services.federation.tables import (
    FederationDirection,
    FederationMountRead,
)


async def resolve_push_mount(
    reg: FederationRegistry, package_name: str
) -> FederationMountRead | None:
    return await reg.resolve_mount_for_package(
        package_name, direction=FederationDirection.PUSH
    )


async def forward_publish(
    *,
    proxy: FederationProxy,
    reg: FederationRegistry,
    mount: FederationMountRead,
    request: Request,
    meta: PublishRequest,
    blob_map: dict[str, bytes],
) -> PublishResult:
    """POST multipart ``/publish`` to the peer; return its PublishResult."""
    hop = parse_incoming_hop(request)
    authorization = await authorization_for_mount(
        reg, mount, hop=hop, for_publish=True
    )
    if not authorization:
        raise HTTPException(
            status_code=502,
            detail=f"push mount {mount.prefix!r} has no credential",
        )
    files: list[tuple[str, tuple[str, bytes, str]]] = [
        ("files", (name, data, "application/octet-stream"))
        for name, data in blob_map.items()
    ]
    try:
        resp = await proxy.forward(
            mount=mount,
            path="/publish",
            method="POST",
            authorization=authorization,
            incoming_hop=hop,
            trace=parse_trace(request),
            data={"meta": meta.model_dump_json()},
            files=files,
        )
    except FederationProxyError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    if resp.status_code >= 400:
        detail: Any
        try:
            body = resp.json()
            detail = body.get("detail", body) if isinstance(body, dict) else body
        except ValueError:
            detail = (resp.text or f"peer returned {resp.status_code}")[:500]
        code = resp.status_code if resp.status_code in (400, 401, 403, 409, 422) else 502
        if code == 502:
            detail = f"peer publish failed ({resp.status_code}): {detail}"
        raise HTTPException(status_code=code, detail=detail)
    try:
        return PublishResult.model_validate(resp.json())
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail="peer returned invalid publish result"
        ) from exc


def push_origin_headers(mount: FederationMountRead) -> dict[str, str]:
    return remote_headers(mount)
