"""High-level federation forward helpers for API routes."""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import HTTPException, Request

from pymergetic.metal.cdn.services.federation.artifact_name import mount_for_artifact
from pymergetic.metal.cdn.services.federation.neg_cache import (
    NegativePeerCache,
    neg_cache_from_request,
)
from pymergetic.metal.cdn.services.federation.proxy import (
    FED_HOP_HEADER,
    FED_MOUNT_HEADER,
    FED_ORIGIN_HEADER,
    FED_TRACE_HEADER,
    FederationProxy,
    FederationProxyError,
)
from pymergetic.metal.cdn.services.federation.registry import FederationRegistry
from pymergetic.metal.cdn.services.federation.scopes import (
    SCOPE_FEDERATION_PUBLISH,
    SCOPE_FEDERATION_READ,
)
from pymergetic.metal.cdn.services.federation.tables import FederationMountRead
from pymergetic.metal.cdn.services.federation.tickets import sign_ticket


def parse_incoming_hop(request: Request) -> int:
    raw = request.headers.get(FED_HOP_HEADER) or "0"
    try:
        return max(0, int(raw))
    except ValueError:
        return 0


def parse_trace(request: Request) -> str | None:
    return request.headers.get(FED_TRACE_HEADER)


def remote_headers(mount: FederationMountRead) -> dict[str, str]:
    return {
        FED_ORIGIN_HEADER: "remote",
        FED_MOUNT_HEADER: mount.prefix,
    }


async def resolve_mount(
    reg: FederationRegistry, package_name: str
) -> FederationMountRead | None:
    return await reg.resolve_mount_for_package(package_name)


async def authorization_for_mount(
    reg: FederationRegistry,
    mount: FederationMountRead,
    *,
    hop: int,
    for_publish: bool = False,
) -> str | None:
    """Prefer MetalFed ticket when an Ed25519 key is installed; else Bearer."""
    scopes = [SCOPE_FEDERATION_READ]
    if for_publish:
        scopes = [SCOPE_FEDERATION_READ, SCOPE_FEDERATION_PUBLISH]
    fed = await reg.get_fed_private_for_mount(mount.id)
    if fed is not None:
        private_b64, kid = fed
        return sign_ticket(
            private_b64,
            prefix=mount.prefix,
            scopes=scopes,
            hop=hop + 1,
            aud=mount.peer_base_url,
            key_id=kid or None,
        )
    bearer = await reg.get_bearer_for_mount(mount.id)
    if bearer:
        return f"Bearer {bearer}"
    return None


def _check_neg(
    cache: NegativePeerCache,
    *,
    mount: FederationMountRead,
    method: str,
    path: str,
    params: dict[str, Any] | None,
) -> str:
    key = NegativePeerCache.key(
        mount_id=str(mount.id), method=method, path=path, params=params
    )
    if cache.is_miss(key):
        raise HTTPException(status_code=404, detail="package not found")
    return key


async def forward_json(
    *,
    proxy: FederationProxy,
    reg: FederationRegistry,
    mount: FederationMountRead,
    path: str,
    request: Request,
    params: dict[str, Any] | None = None,
) -> Any:
    hop = parse_incoming_hop(request)
    authorization = await authorization_for_mount(reg, mount, hop=hop)
    cache = neg_cache_from_request(request)
    neg_key = _check_neg(cache, mount=mount, method="GET", path=path, params=params)
    try:
        resp = await proxy.forward(
            mount=mount,
            path=path,
            method="GET",
            authorization=authorization,
            incoming_hop=hop,
            trace=parse_trace(request),
            params=params,
        )
    except FederationProxyError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    if resp.status_code == 404:
        cache.remember_miss(neg_key)
        raise HTTPException(status_code=404, detail="package not found")
    if resp.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail=f"peer returned {resp.status_code}",
        )
    try:
        return resp.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="peer returned invalid JSON") from exc


async def forward_bytes(
    *,
    proxy: FederationProxy,
    reg: FederationRegistry,
    mount: FederationMountRead,
    path: str,
    request: Request,
    method: str = "GET",
) -> httpx.Response:
    hop = parse_incoming_hop(request)
    authorization = await authorization_for_mount(reg, mount, hop=hop)
    cache = neg_cache_from_request(request)
    neg_key = _check_neg(cache, mount=mount, method=method, path=path, params=None)
    try:
        resp = await proxy.forward(
            mount=mount,
            path=path,
            method=method,
            authorization=authorization,
            incoming_hop=hop,
            trace=parse_trace(request),
        )
    except FederationProxyError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    if resp.status_code == 404:
        cache.remember_miss(neg_key)
    return resp


async def mount_for_filename(
    reg: FederationRegistry, filename: str
) -> FederationMountRead | None:
    mounts = await reg.list_mounts()
    return mount_for_artifact(filename, mounts)
