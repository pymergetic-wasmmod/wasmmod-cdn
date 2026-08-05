"""Jinja-templated browse UI + embedded OpenAPI docs."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from pymergetic.metal.cdn import __version__
from pymergetic.metal.cdn.api.deps import (
    SESSION_USER_KEY,
    IndexServiceDep,
    OptionalUserDep,
    ShellSessionServiceDep,
)
from pymergetic.metal.cdn.db import Database
from pymergetic.metal.cdn.layout import ChannelLayout, ChannelRef
from pymergetic.metal.cdn.middleware.csrf import ensure_csrf_token
from pymergetic.metal.cdn.models import UserRead
from pymergetic.metal.cdn.paths import author_path, channel_path, join_base, package_path
from pymergetic.metal.cdn.services.channel import IndexService
from pymergetic.metal.cdn.services.identity import UserService
from pymergetic.metal.cdn.services.shell_sessions import ensure_principal
from pymergetic.metal.cdn.web.repl_autoexec import render_autoexec
from pymergetic.metal.cdn_client.format import human_size

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.globals["app_name"] = "metal-cdn"
templates.env.globals["app_version"] = __version__
templates.env.globals["project_url"] = "https://github.com/pymergetic/metal-cdn"
templates.env.globals["pypi_url"] = "https://pypi.org/project/pymergetic-metal-cdn/"
templates.env.globals["wasmmod_url"] = "https://github.com/pymergetic/wasmmod"
templates.env.globals["base_path"] = ""
templates.env.filters["human_size"] = human_size
templates.env.globals["human_size"] = human_size

web_router = APIRouter(tags=["web"])

_base_path: str = "/"


def configure_web(base_path: str) -> None:
    """Bind URL helpers to the configured mount prefix."""
    global _base_path
    _base_path = base_path
    templates.env.globals["base_path"] = "" if base_path == "/" else base_path
    templates.env.globals["href"] = lambda *parts: join_base(base_path, *parts)
    templates.env.globals["channel_href"] = lambda channel: join_base(
        base_path, channel_path(channel if isinstance(channel, str) else channel.name)
    )
    templates.env.globals["package_href"] = lambda channel, name: join_base(
        base_path,
        package_path(channel if isinstance(channel, str) else channel.name, name),
    )
    templates.env.globals["author_href"] = lambda email: join_base(
        base_path, author_path(str(email))
    )


configure_web("/")


def _url(*parts: str) -> str:
    return join_base(_base_path, *parts)


def resolve_brand_logo_url(settings: Any, *, default_href: str) -> str:
    """Resolve ``brand_logo_url`` to an absolute or site-relative img src."""
    raw = getattr(settings, "brand_logo_url", None) if settings is not None else None
    if not raw:
        return default_href
    v = str(raw).strip()
    if v.startswith(("http://", "https://", "//")):
        return v
    if v.startswith("/"):
        return v
    # Treat as path under static/, e.g. img/my.png
    parts = [p for p in v.split("/") if p and p != "."]
    if not parts:
        return default_href
    if parts[0] != "static":
        parts = ["static", *parts]
    return _url(*parts)


def _cdn_base_url(request: Request) -> str:
    """Absolute CDN root for wasm.cdn (scheme://host[/base_path]).

    Prefer ``?cdn=`` from the shell UI (``data-cdn-base``) when present; otherwise
    derive from the autoexec request itself.
    """
    q = (request.query_params.get("cdn") or "").strip().rstrip("/")
    if q.startswith(("http://", "https://")) and " " not in q and len(q) < 512:
        return q
    origin = f"{request.url.scheme}://{request.url.netloc}"
    if _base_path in ("", "/"):
        return origin
    return origin + _base_path.rstrip("/")


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


async def _session_user(request: Request) -> UserRead | None:
    raw = request.session.get(SESSION_USER_KEY)
    if not raw:
        return None
    try:
        user_id = UUID(str(raw))
    except ValueError:
        return None
    db: Database | None = getattr(request.app.state, "db", None)
    if db is None:
        return None
    async for session in db.session():
        user = await UserService(session).get(user_id)
        if user is not None and user.is_active:
            return user
        return None
    return None


async def _shell_context(
    indexes: IndexService,
    *,
    active_channel: str,
    active_package: str | None = None,
    page: str = "browse",
    request: Request | None = None,
    current_user: UserRead | None = None,
) -> dict[str, Any]:
    catalog = await indexes.list_catalog()
    nav_roots = await indexes.browse_package_nav()
    package_versions: list = []
    if active_package:
        package_versions = await indexes.package_versions(active_package)
    experimental = False
    experimental_message: str | None = None
    experimental_repl = False
    repl_ready = False
    repl_asset_v = ""
    site_css_v = ""
    inspect_js_v = ""
    bootstrap_admin_email: str | None = None
    brand_name = "metal-cdn"
    brand_logo_url = _url("static", "img", "pymergetic.png")
    public_origin: str | None = None
    settings = None
    try:
        static_dir = Path(__file__).resolve().parent / "static"
        css = static_dir / "site.css"
        mtimes_css: list[int] = []
        if css.is_file():
            mtimes_css.append(int(css.stat().st_mtime))
        css_dir = static_dir / "css"
        if css_dir.is_dir():
            for child in css_dir.glob("*.css"):
                try:
                    mtimes_css.append(int(child.stat().st_mtime))
                except OSError:
                    pass
        if mtimes_css:
            site_css_v = format(max(mtimes_css), "x")
        inspect_js = static_dir / "inspect.js"
        inspect_main = static_dir / "inspect" / "main.js"
        mtimes: list[int] = []
        if inspect_js.is_file():
            mtimes.append(int(inspect_js.stat().st_mtime))
        if inspect_main.is_file():
            mtimes.append(int(inspect_main.stat().st_mtime))
        inspect_dir = static_dir / "inspect"
        if inspect_dir.is_dir():
            for child in inspect_dir.glob("*.js"):
                try:
                    mtimes.append(int(child.stat().st_mtime))
                except OSError:
                    pass
        if mtimes:
            inspect_js_v = format(max(mtimes), "x")
    except OSError:
        site_css_v = ""
        inspect_js_v = ""
    if request is not None:
        settings = getattr(request.app.state, "settings", None)
        if settings is not None and getattr(settings, "experimental", False):
            experimental = True
            experimental_message = getattr(settings, "experimental_message", None)
        if settings is not None and getattr(settings, "experimental_repl", False):
            experimental_repl = True
            repl_dir = Path(__file__).resolve().parent / "static" / "repl"
            repl_mjs = repl_dir / "micropython.mjs"
            repl_wasm = repl_dir / "micropython.wasm"
            repl_ready = repl_mjs.is_file()
            # Cache-bust query for mjs (+ locateFile wasm) across deploys.
            try:
                mtimes = []
                if repl_mjs.is_file():
                    mtimes.append(int(repl_mjs.stat().st_mtime))
                if repl_wasm.is_file():
                    mtimes.append(int(repl_wasm.stat().st_mtime))
                if mtimes:
                    repl_asset_v = format(max(mtimes), "x")
            except OSError:
                repl_asset_v = ""
        if settings is not None and getattr(settings, "bootstrap_admin_email", None):
            bootstrap_admin_email = str(settings.bootstrap_admin_email)
        if settings is not None:
            brand_name = getattr(settings, "display_brand_name", None) or brand_name
            brand_logo_url = resolve_brand_logo_url(
                settings, default_href=_url("static", "img", "pymergetic.png")
            )
            public_origin = getattr(settings, "public_origin", None)
        if current_user is None:
            current_user = await _session_user(request)
    return {
        "catalog": catalog,
        "nav_roots": nav_roots,
        "package_versions": package_versions,
        "active_channel": active_channel,
        "active_package": active_package,
        "active_page": page,
        "experimental": experimental,
        "experimental_message": experimental_message,
        "experimental_repl": experimental_repl,
        "repl_ready": repl_ready,
        "repl_asset_v": repl_asset_v,
        "site_css_v": site_css_v,
        "inspect_js_v": inspect_js_v,
        "bootstrap_admin_email": bootstrap_admin_email,
        "current_user": current_user,
        "brand_name": brand_name,
        "brand_logo_url": brand_logo_url,
        "public_origin": public_origin,
    }


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
    # Cookie principal id (Starlette session anon_id / user_id) — not the shell SESSION_ID.
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


async def _channel_page(
    request: Request,
    indexes: IndexService,
    ref: ChannelRef,
) -> HTMLResponse:
    ctx = await _shell_context(indexes, active_channel=ref.name, request=request)
    ctx.update({"channel": ref.name})
    return templates.TemplateResponse(request, "home.html", ctx)


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
    if entry is None:
        raise HTTPException(status_code=404, detail="package not found")
    ctx = await _shell_context(
        indexes, active_channel=ref.name, active_package=name, request=request
    )
    ctx.update({
        "name": name,
        "entry": entry,
        "channel": ref.name,
        "needed_by": await indexes.list_dependents(name),
        "deps_ok": await indexes.deps_fit(entry.deps),
    })
    return templates.TemplateResponse(request, "package.html", ctx)
