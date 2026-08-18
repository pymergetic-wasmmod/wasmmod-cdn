"""URL helpers, brand logo resolution, and utemplate render factory.

Replaces the Jinja2Templates environment with the shared micro utemplate render
layer in :mod:`render`. The browse UI is one source of templates rendered by
both the CDN (here) and the on-device metal seat (which calls the same functions
against its own vendored copy).
"""

from __future__ import annotations

from typing import Any

from fastapi import Request

from pymergetic.wasmmod.cdn import __version__
from pymergetic.wasmmod.cdn.paths import channel_path, package_path
from pymergetic.wasmmod.cdn.web import render as _render


def configure_web(base_path: str) -> None:
    """Bind URL helpers to the configured mount prefix."""
    _render.configure_web(base_path)


def href(*parts: str) -> str:
    return _render.href(*parts)


def channel_href(channel) -> str:
    return _render.href(channel_path(channel if isinstance(channel, str) else channel.name))


def package_href(channel, name) -> str:
    return _render.href(package_path(channel if isinstance(channel, str) else channel.name, name))


def _url(*parts: str) -> str:
    return _render._url(*parts)


def render_page(name: str, ctx: dict) -> str:
    """Render a body template wrapped in the shared shell (server-side)."""
    return _render.render_page(name, ctx)


configure_web("/")


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
    base = getattr(_render, "_base_path", "/")
    if base in ("", "/"):
        return origin
    return origin + base.rstrip("/")
