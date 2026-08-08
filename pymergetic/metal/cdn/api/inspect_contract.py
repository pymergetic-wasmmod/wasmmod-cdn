"""Shared Inspect contract routes via metal FastAPIAdapter.

CDN already owns GET /health (HealthResponse). Adapter mounts capabilities +
self-desc + stubs with include_health=False, then shared www at /inspect.

Monorepo: pymergetic + pymergetic.metal are PEP 420 namespaces — append the
metal src portion onto pymergetic.metal.__path__ (never seal with __init__.py).
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.staticfiles import StaticFiles

log = logging.getLogger(__name__)


def _packages_root() -> Path:
    # .../packages/metal-cdn/pymergetic/metal/cdn/api/inspect_contract.py
    # parents: api, cdn, metal, pymergetic, metal-cdn, packages
    return Path(__file__).resolve().parents[5]


def _ensure_metal_inspect_path() -> None:
    """Add metal's inspect as another PEP 420 portion of pymergetic.metal."""
    try:
        import pymergetic.metal.inspect.adapter_fastapi  # noqa: F401

        return
    except ImportError:
        pass

    metal_portion = (
        _packages_root()
        / "metalpython"
        / "extmod"
        / "metal"
        / "src"
        / "pymergetic"
        / "metal"
    )
    if not metal_portion.is_dir():
        return

    import pymergetic
    import pymergetic.metal as metal_pkg

    pym_portion = str(metal_portion.parent)  # .../src/pymergetic
    metal_path = str(metal_portion)
    if pym_portion not in pymergetic.__path__:
        pymergetic.__path__.append(pym_portion)
    if metal_path not in metal_pkg.__path__:
        metal_pkg.__path__.append(metal_path)


def _inspect_www_dir() -> Path | None:
    www = (
        _packages_root()
        / "metalpython"
        / "extmod"
        / "metal"
        / "src"
        / "pymergetic"
        / "metal"
        / "inspect"
        / "www"
        / "inspect"
    )
    return www if www.is_dir() else None


def mount_inspect_contract(app: FastAPI, *, prefix: str = "") -> None:
    """Register Inspect contract + shared www under optional path prefix (e.g. /cdn)."""
    _ensure_metal_inspect_path()
    from pymergetic.metal.inspect.adapter_fastapi import FastAPIAdapter

    router = APIRouter()
    FastAPIAdapter(role="cdn", theme="cdn", app=router, include_health=False)
    # API routes before StaticFiles mount so /inspect/self is not swallowed.
    app.include_router(router, prefix=prefix)

    www = _inspect_www_dir()
    if www is None:
        log.warning("metal Inspect www missing; skipping /inspect static mount")
        return
    mount_path = f"{prefix.rstrip('/')}/inspect" if prefix else "/inspect"
    app.mount(
        mount_path,
        StaticFiles(directory=str(www), html=True),
        name="inspect_www",
    )
