"""High-level federation forward helpers for API routes."""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import HTTPException, Request

from pymergetic.metal.cdn.services.federation.artifact_name import mount_for_artifact
from pymergetic.metal.cdn.services.federation.proxy import (
    FED_HOP_HEADER,
    FED_MOUNT_HEADER,
    FED_ORIGIN_HEADER,
    FED_TRACE_HEADER,
    FederationProxy,
    FederationProxyError,
)
from pymergetic.metal.cdn.services.federation.registry import FederationRegistry
from pymergetic.metal.cdn.services.federation.tables import FederationMountRead


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


async def forward_json(
    *,
    proxy: FederationProxy,
    reg: FederationRegistry,
    mount: FederationMountRead,
    path: str,
    request: Request,
    params: dict[str, Any] | None = None,
) -> Any:
    bearer = await reg.get_bearer_for_mount(mount.id)
    try:
        resp = await proxy.forward(
            mount=mount,
            path=path,
            method="GET",
            bearer=bearer,
            incoming_hop=parse_incoming_hop(request),
            trace=parse_trace(request),
            params=params,
        )
    except FederationProxyError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    if resp.status_code == 404:
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
    bearer = await reg.get_bearer_for_mount(mount.id)
    try:
        return await proxy.forward(
            mount=mount,
            path=path,
            method=method,
            bearer=bearer,
            incoming_hop=parse_incoming_hop(request),
            trace=parse_trace(request),
        )
    except FederationProxyError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


async def mount_for_filename(
    reg: FederationRegistry, filename: str
) -> FederationMountRead | None:
    mounts = await reg.list_mounts()
    return mount_for_artifact(filename, mounts)
