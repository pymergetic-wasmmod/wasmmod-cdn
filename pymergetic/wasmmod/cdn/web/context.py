"""Jinja env, URL helpers, brand logo resolution."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates

from pymergetic.wasmmod.cdn import __version__
from pymergetic.wasmmod.cdn.paths import author_path, channel_path, join_base, package_path
from pymergetic.wasmmod.cdn_client.format import human_size

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.globals["app_name"] = "wasmmod-cdn"
templates.env.globals["app_version"] = __version__
templates.env.globals["project_url"] = "https://github.com/pymergetic-wasmmod/wasmmod-cdn"
templates.env.globals["pypi_url"] = "https://pypi.org/project/pymergetic-wasmmod-cdn/"
templates.env.globals["wasmmod_url"] = "https://github.com/pymergetic-wasmmod/wasmmod"
templates.env.globals["base_path"] = ""
templates.env.filters["human_size"] = human_size
templates.env.globals["human_size"] = human_size

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
