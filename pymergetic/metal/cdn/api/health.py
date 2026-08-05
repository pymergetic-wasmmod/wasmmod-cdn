"""Health / readiness / status endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text

from pymergetic.metal.cdn import __version__
from pymergetic.metal.cdn.api.deps import SettingsDep, StorageDep, get_db
from pymergetic.metal.cdn.db import Database
from pymergetic.metal.cdn.models import HealthResponse, ReadyResponse, StatusResponse

health_router = APIRouter(tags=["health"])


@health_router.get("/health", response_model=HealthResponse)
async def health(settings: SettingsDep) -> HealthResponse:
    """Liveness — process is up (no dependency checks)."""
    return HealthResponse(
        status="ok",
        version=__version__,
        experimental=settings.experimental,
        experimental_message=settings.experimental_message if settings.experimental else None,
    )


@health_router.get("/status", response_model=StatusResponse)
async def deployment_status(settings: SettingsDep) -> StatusResponse:
    """Public deployment flags (experimental banner, version)."""
    return StatusResponse(
        version=__version__,
        experimental=settings.experimental,
        experimental_message=settings.experimental_message if settings.experimental else None,
    )


@health_router.get("/ready", response_model=ReadyResponse)
async def ready(
    settings: SettingsDep,
    storage: StorageDep,
    db: Annotated[Database, Depends(get_db)],
) -> ReadyResponse:
    """Readiness — database + storage usable."""
    db_status = "ok"
    storage_status = "ok"
    try:
        async with db.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:
        db_status = f"error: {exc}"
    try:
        probe = "__ready_probe__"
        await storage.put_bytes(probe, b"ok")
        await storage.delete(probe)
    except Exception as exc:
        storage_status = f"error: {exc}"
    ok = db_status == "ok" and storage_status == "ok"
    if not ok:
        raise HTTPException(
            status_code=503,
            detail={
                "status": "not_ready",
                "version": __version__,
                "database": db_status,
                "storage": storage_status,
            },
        )
    return ReadyResponse(
        status="ok",
        version=__version__,
        database=db_status,
        storage=storage_status,
        experimental=settings.experimental,
    )
