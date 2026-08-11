"""Shared Inspect contract routes via metal FastAPIAdapter.

CDN already owns GET /health (HealthResponse). Adapter mounts capabilities +
self-desc + stubs with include_health=False, then shared www at /inspect.

Prefer in-tree `pymergetic.metal.inspect` (synced by scripts/sync-metal-inspect.sh
for Docker). Fall back to appending the metalpython source portion (monorepo).
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.staticfiles import StaticFiles

log = logging.getLogger(__name__)


def _packages_root() -> Path:
    # .../packages/wasmmod-cdn/pymergetic/wasmmod/cdn/api/inspect_contract.py
    return Path(__file__).resolve().parents[5]


def _metal_pkg_dir() -> Path:
    # .../pymergetic/wasmmod/cdn/api/this.py, parents[3] = .../pymergetic.
    # pymergetic/metal is a namespace sibling of pymergetic/wasmmod, not an
    # ancestor of this file — metal.inspect is genuine Metal-kernel content
    # (synced by scripts/sync-metal-inspect.sh), separate from wasmmod-cdn's
    # own pymergetic.wasmmod.cdn* namespace.
    return Path(__file__).resolve().parents[3] / "metal"


def _ensure_metal_inspect_path() -> None:
    """Ensure pymergetic.metal.inspect is importable (in-tree or monorepo)."""
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
    # 1) Vendored next to this package (Docker / synced tree).
    local = _metal_pkg_dir() / "inspect" / "www" / "inspect"
    if local.is_dir():
        return local
    # 2) Monorepo sibling metalpython.
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
    try:
        from pymergetic.metal.inspect.adapter_fastapi import FastAPIAdapter
    except ImportError as e:
        raise RuntimeError(
            "pymergetic.metal.inspect missing — run scripts/sync-metal-inspect.sh "
            "(or keep a metalpython sibling checkout)"
        ) from e

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
