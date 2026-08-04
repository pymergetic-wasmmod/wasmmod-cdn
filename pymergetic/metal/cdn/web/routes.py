"""Jinja-templated browse UI + embedded OpenAPI docs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from pymergetic.metal.cdn import __version__
from pymergetic.metal.cdn.api.deps import IndexServiceDep
from pymergetic.metal.cdn.layout import ChannelLayout, ChannelRef
from pymergetic.metal.cdn.paths import author_path, channel_path, join_base, package_path
from pymergetic.metal.cdn.services.channel import IndexService
from pymergetic.metal.cdn_client.format import human_size

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.globals["app_name"] = "metal-cdn"
templates.env.globals["app_version"] = __version__
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


async def _shell_context(
    indexes: IndexService,
    *,
    active_channel: str,
    active_package: str | None = None,
    page: str = "browse",
    request: Request | None = None,
) -> dict[str, Any]:
    catalog = await indexes.list_catalog()
    nav_roots = await indexes.browse_package_nav()
    package_versions: list = []
    if active_package:
        package_versions = await indexes.package_versions(active_package)
    experimental = False
    experimental_message: str | None = None
    if request is not None:
        settings = getattr(request.app.state, "settings", None)
        if settings is not None and getattr(settings, "experimental", False):
            experimental = True
            experimental_message = getattr(settings, "experimental_message", None)
    return {
        "catalog": catalog,
        "nav_roots": nav_roots,
        "package_versions": package_versions,
        "active_channel": active_channel,
        "active_package": active_package,
        "active_page": page,
        "experimental": experimental,
        "experimental_message": experimental_message,
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
