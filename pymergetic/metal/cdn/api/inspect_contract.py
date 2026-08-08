"""Shared Inspect contract routes via metal FastAPIAdapter.

CDN already owns GET /health (HealthResponse). Adapter mounts capabilities +
NotImplemented stubs with include_health=False.
"""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi import APIRouter, FastAPI


def _ensure_metal_inspect_path() -> None:
    """Dev/monorepo: metal inspect Py lives under metalpython/extmod/metal/src."""
    try:
        import pymergetic.metal.inspect.adapter_fastapi  # noqa: F401

        return
    except ImportError:
        pass
    here = Path(__file__).resolve()
    # .../packages/metal-cdn/pymergetic/metal/cdn/api/inspect_contract.py
    # parents: api, cdn, metal, pymergetic, metal-cdn, packages
    packages = here.parents[5]
    metal_src = packages / "metalpython" / "extmod" / "metal" / "src"
    if metal_src.is_dir():
        p = str(metal_src)
        if p not in sys.path:
            sys.path.insert(0, p)


def mount_inspect_contract(app: FastAPI, *, prefix: str = "") -> None:
    """Register Inspect contract under optional path prefix (e.g. /cdn)."""
    _ensure_metal_inspect_path()
    from pymergetic.metal.inspect.adapter_fastapi import FastAPIAdapter

    router = APIRouter()
    FastAPIAdapter(role="cdn", theme="cdn", app=router, include_health=False)
    app.include_router(router, prefix=prefix)
