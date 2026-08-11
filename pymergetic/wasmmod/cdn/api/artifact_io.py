"""Artifact byte load / cache Response helpers (local + federation forward)."""

from __future__ import annotations

import hashlib

from fastapi import HTTPException, Request
from fastapi.responses import Response

from pymergetic.wasmmod.cdn.api.deps import SettingsDep, StorageDep
from pymergetic.wasmmod.cdn.api.fed_deps import FederationProxyDep, FederationRegistryDep
from pymergetic.wasmmod.cdn.layout import ChannelLayout
from pymergetic.wasmmod.cdn.services.federation.forward import (
    forward_bytes,
    mount_for_filename,
    remote_headers,
)


def _media_type_for_artifact(filename: str = "") -> str:
    """MIME for browser-loadable seat artifacts (ESM / Wasm); else octet-stream."""
    low = (filename or "").lower().removesuffix(".zlib")
    if low.endswith(".mjs"):
        return "text/javascript"
    if low.endswith(".wasm"):
        return "application/wasm"
    return "application/octet-stream"


def _artifact_response(
    data: bytes,
    *,
    request: Request,
    cache_s: int,
    immutable: bool,
    filename: str = "",
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
    media_type = _media_type_for_artifact(filename)
    # Browser wasmmod probes with HEAD (falls back to GET on 405); answer both.
    if request.method.upper() == "HEAD":
        return Response(status_code=200, media_type=media_type, headers=headers)
    return Response(
        content=data,
        media_type=media_type,
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


async def _load_artifact_bytes_fed(
    *,
    storage: StorageDep,
    request: Request,
    reg: FederationRegistryDep,
    proxy: FederationProxyDep,
    channel: str,
    filename: str,
    peer_path: str,
) -> bytes:
    try:
        return await _load_artifact_bytes(storage, channel=channel, filename=filename)
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        mount = await mount_for_filename(reg, filename)
        if mount is None:
            raise
        peer = await forward_bytes(
            proxy=proxy,
            reg=reg,
            mount=mount,
            path=peer_path,
            request=request,
            method="GET",
        )
        if peer.status_code == 404:
            raise HTTPException(status_code=404, detail="artifact not found") from None
        if peer.status_code >= 400:
            raise HTTPException(
                status_code=502, detail=f"peer returned {peer.status_code}"
            ) from None
        return peer.content


async def _artifact_or_forward(
    *,
    storage: StorageDep,
    request: Request,
    settings: SettingsDep,
    reg: FederationRegistryDep,
    proxy: FederationProxyDep,
    channel: str,
    filename: str,
    peer_path: str,
) -> Response:
    try:
        data = await _load_artifact_bytes(storage, channel=channel, filename=filename)
        mount = None
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        mount = await mount_for_filename(reg, filename)
        if mount is None:
            raise
        peer = await forward_bytes(
            proxy=proxy,
            reg=reg,
            mount=mount,
            path=peer_path,
            request=request,
            method="GET",  # always GET body for ETag; answer client HEAD below
        )
        if peer.status_code == 404:
            raise HTTPException(status_code=404, detail="artifact not found") from None
        if peer.status_code >= 400:
            raise HTTPException(
                status_code=502, detail=f"peer returned {peer.status_code}"
            ) from None
        data = peer.content
    cache_s = settings.artifact_cache_lead_s if channel == "lead" else settings.artifact_cache_pin_s
    resp = _artifact_response(
        data,
        request=request,
        cache_s=cache_s,
        immutable=False,
        filename=filename,
    )
    if mount is not None:
        for k, v in remote_headers(mount).items():
            resp.headers[k] = v
    return resp


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


def _section_raw_response(body: bytes, *, index: int, name: str, offset: int = 0) -> Response:
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
