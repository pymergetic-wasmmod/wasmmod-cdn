"""Catalog page data helpers (local + federation)."""

from __future__ import annotations

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse

from pymergetic.wasmmod.cdn.db import Database
from pymergetic.wasmmod.cdn.layout import ChannelLayout, ChannelRef
from pymergetic.wasmmod.cdn.models import PackageEntry
from pymergetic.wasmmod.cdn.services.index_service import package_role
from pymergetic.wasmmod.cdn.services.channel import IndexService
from pymergetic.wasmmod.cdn.services.federation.catalog import enrich_shell_lists
from pymergetic.wasmmod.cdn.services.federation.forward import forward_json, resolve_mount
from pymergetic.wasmmod.cdn.services.federation.proxy import FederationProxy
from pymergetic.wasmmod.cdn.services.federation.registry import FederationRegistry
from pymergetic.wasmmod.cdn.web.context import _cdn_base_url
from pymergetic.wasmmod.cdn.web.shell_ctx import _shell_context

from pymergetic.wasmmod.cdn.web import render as _render


async def _merge_fed_catalog(
    request: Request, catalog: list, nav_roots: list
) -> tuple[list, list]:
    settings = getattr(request.app.state, "settings", None)
    db: Database | None = getattr(request.app.state, "db", None)
    if settings is None or db is None:
        return catalog, nav_roots
    secret = (settings.federation_secrets_key or settings.session_secret or "").strip()
    if not secret:
        return catalog, nav_roots
    client = getattr(request.app.state, "federation_http_client", None)
    proxy = FederationProxy(
        client=client,
        max_hops=settings.federation_max_hops,
        allow_private_net=settings.federation_allow_private_net,
    )
    try:
        async with db.session_maker() as session:
            reg = FederationRegistry(
                session, secrets_key=secret, max_hops=settings.federation_max_hops
            )
            if not await reg.list_mounts():
                return catalog, nav_roots
            return await enrich_shell_lists(
                request, reg=reg, proxy=proxy, catalog=catalog
            )
    finally:
        if client is None:
            await proxy.aclose()


async def _channel_page(
    request: Request,
    indexes: IndexService,
    ref: ChannelRef,
) -> HTMLResponse:
    ctx = await _shell_context(indexes, active_channel=ref.name, request=request)
    ctx.update({"channel": ref.name, "active_channel": ref.name})
    return HTMLResponse(_render.render_page("home.html", ctx))


async def _package_page(
    request: Request,
    indexes: IndexService,
    ref: ChannelRef,
    name: str,
) -> HTMLResponse:
    try:
        ChannelLayout.validate_package_name(name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    entry = await indexes.get_package(ref, name)
    fed_origin = "local"
    fed_peer_label: str | None = None
    fed_peer_browse_url: str | None = None
    if entry is None:
        entry, fed_origin, fed_peer_label, fed_peer_browse_url = await _fetch_remote_package(
            request, name, channel=ref.name
        )
        if entry is None:
            raise HTTPException(status_code=404, detail="package not found")
    ctx = await _shell_context(
        indexes, active_channel=ref.name, active_package=name, request=request
    )
    ctx.update(
        {
            "name": name,
            "entry": entry,
            "channel": ref.name,
            "needed_by": await indexes.list_dependents(name),
            "deps_ok": await indexes.deps_fit(entry.deps),
            "fed_origin": fed_origin,
            "fed_peer_label": fed_peer_label,
            "fed_peer_browse_url": fed_peer_browse_url,
            "package_role": package_role(name, entry),
            "cdn_base": _cdn_base_url(request),
            "is_arch_seat": name.startswith("pymergetic.metal.arch."),
            "is_unix_seat": name.startswith("pymergetic.metal.unix."),
            "author_href": _render.href("channels", "lead", "authors", entry.maintainer_email),
        }
    )
    ctx = _render.shape_package(ctx)
    return HTMLResponse(_render.render_page("package.html", ctx))


async def _fetch_remote_package(
    request: Request, name: str, *, channel: str
) -> tuple[PackageEntry | None, str, str | None, str | None]:
    settings = getattr(request.app.state, "settings", None)
    db: Database | None = getattr(request.app.state, "db", None)
    if settings is None or db is None:
        return None, "local", None, None
    secret = (settings.federation_secrets_key or settings.session_secret or "").strip()
    if not secret:
        return None, "local", None, None
    client = getattr(request.app.state, "federation_http_client", None)
    proxy = FederationProxy(
        client=client,
        max_hops=settings.federation_max_hops,
        allow_private_net=settings.federation_allow_private_net,
    )
    try:
        async with db.session_maker() as session:
            reg = FederationRegistry(
                session, secrets_key=secret, max_hops=settings.federation_max_hops
            )
            mount = await resolve_mount(reg, name)
            if mount is None:
                return None, "local", None, None
            data = await forward_json(
                proxy=proxy,
                reg=reg,
                mount=mount,
                path=f"/packages/{name}",
                request=request,
                params={"channel": channel},
            )
            entry = PackageEntry.model_validate(data)
            browse = (
                f"{(mount.peer_base_url or '').rstrip('/')}/channels/lead/packs/{name}"
                if mount.peer_base_url
                else None
            )
            return entry, "remote", mount.peer_label, browse
    except HTTPException:
        return None, "local", None, None
    finally:
        if client is None:
            await proxy.aclose()
