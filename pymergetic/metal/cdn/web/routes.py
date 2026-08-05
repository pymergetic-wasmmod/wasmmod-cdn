"""Jinja-templated browse UI + embedded OpenAPI docs."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse

from pymergetic.metal.cdn import __version__
from pymergetic.metal.cdn.api.deps import IndexServiceDep, OptionalUserDep, ShellSessionServiceDep
from pymergetic.metal.cdn.middleware.csrf import ensure_csrf_token
from pymergetic.metal.cdn.services.channel import IndexService
from pymergetic.metal.cdn.services.shell_sessions import ensure_principal
from pymergetic.metal.cdn.web.context import (
    _cdn_base_url,
    _url,
    configure_web,
    resolve_brand_logo_url,
    templates,
)
from pymergetic.metal.cdn.web.page_helpers import _channel_page, _package_page
from pymergetic.metal.cdn.web.repl_autoexec import render_autoexec
from pymergetic.metal.cdn.web.shell_ctx import _shell_context

__all__ = ["configure_web", "resolve_brand_logo_url", "web_router"]

web_router = APIRouter(tags=["web"])


@web_router.get(
    "/repl/autoexec.py",
    response_class=PlainTextResponse,
    include_in_schema=True,
    tags=["repl"],
    summary="Browser REPL session bootstrap (Python)",
)
async def repl_autoexec(
    request: Request,
    indexes: IndexServiceDep,
    shells: ShellSessionServiceDep,
    user: OptionalUserDep,
) -> PlainTextResponse:
    """Return ``autoexec.py``: wasm.cdn + install_hook, intro, packages()/help()."""
    settings = getattr(request.app.state, "settings", None)
    if settings is not None and not getattr(settings, "experimental_repl", True):
        raise HTTPException(status_code=404, detail="experimental_repl disabled")
    catalog = await indexes.list_catalog()
    names = sorted({row.name for row in catalog})
    cdn_base = _cdn_base_url(request)
    user_id, anon_id, principal = ensure_principal(request, user)
    ensure_csrf_token(request)
    ua = (request.headers.get("user-agent") or "")[:512]
    shell = await shells.ensure_session(
        user_id=user_id,
        anon_id=anon_id,
        cdn_base=cdn_base,
        channel="lead",
        driver="metal-cdn",
        hook_on=True,
        user_agent=ua,
        principal_label=principal,
    )
    await shells.record_event(
        shell.id,
        kind="autoexec",
        path="/repl/autoexec.py",
    )
    script = render_autoexec(
        cdn_base=cdn_base,
        app_version=__version__,
        packages=names,
        channel="lead",
        session_id=str(shell.id),
        principal=principal,
        driver=shell.driver or "metal-cdn",
    )
    return PlainTextResponse(
        script,
        media_type="text/x-python; charset=utf-8",
        headers={"Cache-Control": "no-store", "X-Shell-Session-Id": str(shell.id)},
    )


@web_router.get("/", include_in_schema=False)
async def home() -> RedirectResponse:
    return RedirectResponse(url=_url("channels/lead"), status_code=307)


@web_router.get("/channels/lead", response_class=HTMLResponse)
async def channel_lead(request: Request, indexes: IndexServiceDep) -> HTMLResponse:
    return await _channel_page(request, indexes, IndexService.parse_channel("lead"))


@web_router.get("/channels/pin/{version}", response_class=HTMLResponse)
async def channel_pin(request: Request, version: str, indexes: IndexServiceDep) -> HTMLResponse:
    try:
        ref = IndexService.parse_channel(f"@{version}")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return await _channel_page(request, indexes, ref)


@web_router.get("/channels/lead/packs/{name:path}", response_class=HTMLResponse)
async def package_lead(request: Request, name: str, indexes: IndexServiceDep) -> HTMLResponse:
    return await _package_page(request, indexes, IndexService.parse_channel("lead"), name)


@web_router.get("/channels/pin/{version}/packs/{name:path}", response_class=HTMLResponse)
async def package_pin(
    request: Request,
    version: str,
    name: str,
    indexes: IndexServiceDep,
) -> HTMLResponse:
    try:
        ref = IndexService.parse_channel(f"@{version}")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return await _package_page(request, indexes, ref, name)


@web_router.get("/authors", response_class=HTMLResponse)
async def authors_page(request: Request, indexes: IndexServiceDep) -> HTMLResponse:
    maintainers = await indexes.list_maintainers()
    ctx = await _shell_context(indexes, active_channel="lead", page="users", request=request)
    ctx.update({"maintainers": maintainers})
    return templates.TemplateResponse(request, "users.html", ctx)


@web_router.get("/authors/{email}", response_class=HTMLResponse)
async def author_page(request: Request, email: str, indexes: IndexServiceDep) -> HTMLResponse:
    packages = await indexes.packages_by_maintainer(email)
    ctx = await _shell_context(indexes, active_channel="lead", page="users", request=request)
    ctx.update({"author_email": email, "packages": packages})
    return templates.TemplateResponse(request, "author.html", ctx)


@web_router.get("/docs", response_class=HTMLResponse, include_in_schema=False)
async def api_docs(request: Request, indexes: IndexServiceDep) -> HTMLResponse:
    ctx = await _shell_context(indexes, active_channel="lead", page="docs", request=request)
    return templates.TemplateResponse(request, "docs.html", ctx)


@web_router.get("/login", response_class=HTMLResponse, include_in_schema=False)
async def login_page(request: Request, indexes: IndexServiceDep) -> HTMLResponse:
    ctx = await _shell_context(indexes, active_channel="lead", page="login", request=request)
    return templates.TemplateResponse(request, "login.html", ctx)


@web_router.get("/publish", response_class=HTMLResponse, include_in_schema=False)
async def publish_page(request: Request, indexes: IndexServiceDep) -> HTMLResponse:
    ctx = await _shell_context(indexes, active_channel="lead", page="publish", request=request)
    return templates.TemplateResponse(request, "publish.html", ctx)


@web_router.get("/sessions", response_class=HTMLResponse, include_in_schema=False)
async def sessions_page(
    request: Request,
    indexes: IndexServiceDep,
    shells: ShellSessionServiceDep,
    user: OptionalUserDep,
) -> HTMLResponse:
    user_id, anon_id, label = ensure_principal(request, user)
    ensure_csrf_token(request)
    rows = await shells.list_mine(user_id=user_id, anon_id=anon_id, principal_label=label)
    activities: dict[str, Any] = {}
    for row in rows[:12]:
        activities[str(row.id)] = await shells.activity(row.id, window_minutes=30)
    principal_web_id = str(user_id or anon_id or "")
    ctx = await _shell_context(
        indexes, active_channel="lead", page="sessions", request=request, current_user=user
    )
    ctx.update(
        {
            "shell_sessions": rows,
            "shell_activities": activities,
            "principal_label": label,
            "principal_web_id": principal_web_id,
        }
    )
    return templates.TemplateResponse(request, "sessions.html", ctx)
