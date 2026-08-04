"""Shell session list / activity / event APIs."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Request

from pymergetic.metal.cdn.api.deps import OptionalUserDep, ShellSessionServiceDep
from pymergetic.metal.cdn.middleware.csrf import ensure_csrf_token
from pymergetic.metal.cdn.models import (
    ShellActivityResponse,
    ShellSessionEventCreate,
    ShellSessionEventRead,
    ShellSessionRead,
)
from pymergetic.metal.cdn.services.shell_sessions import ensure_principal

sessions_router = APIRouter(prefix="/api/sessions", tags=["sessions"])


@sessions_router.get("", response_model=list[ShellSessionRead])
async def list_sessions(
    request: Request,
    shells: ShellSessionServiceDep,
    user: OptionalUserDep,
) -> list[ShellSessionRead]:
    user_id, anon_id, label = ensure_principal(request, user)
    ensure_csrf_token(request)
    return await shells.list_mine(
        user_id=user_id, anon_id=anon_id, principal_label=label
    )


@sessions_router.get("/{session_id}/activity", response_model=ShellActivityResponse)
async def session_activity(
    session_id: UUID,
    request: Request,
    shells: ShellSessionServiceDep,
    user: OptionalUserDep,
    window: int = 30,
) -> ShellActivityResponse:
    user_id, anon_id, _ = ensure_principal(request, user)
    row = await shells.get_owned(session_id, user_id=user_id, anon_id=anon_id)
    if row is None:
        raise HTTPException(status_code=404, detail="session not found")
    return await shells.activity(session_id, window_minutes=window)


@sessions_router.post("/events", response_model=ShellSessionEventRead)
async def post_session_event(
    body: ShellSessionEventCreate,
    request: Request,
    shells: ShellSessionServiceDep,
    user: OptionalUserDep,
) -> ShellSessionEventRead:
    user_id, anon_id, label = ensure_principal(request, user)
    ensure_csrf_token(request)
    if body.session_id is not None:
        row = await shells.get_owned(body.session_id, user_id=user_id, anon_id=anon_id)
        if row is None:
            raise HTTPException(status_code=404, detail="session not found")
        session_id = row.id
    else:
        active = await shells.ensure_session(
            user_id=user_id,
            anon_id=anon_id,
            principal_label=label,
        )
        session_id = active.id
    kind = body.kind.strip().lower()
    if kind not in ("autoexec", "try_package", "import", "pack", "index", "other"):
        kind = "other"
    return await shells.record_event(
        session_id,
        kind=kind,
        path=body.path,
        package=body.package,
    )
