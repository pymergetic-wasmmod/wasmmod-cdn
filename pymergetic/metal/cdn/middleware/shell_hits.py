"""Attribute same-origin CDN GETs to the active shell session."""

from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from pymergetic.metal.cdn.api.deps import SESSION_USER_KEY
from pymergetic.metal.cdn.services.shell_sessions import SESSION_ANON_KEY, ShellSessionService


class ShellHitMiddleware(BaseHTTPMiddleware):
    """After successful GET pack/index/autoexec, record a shell_session event."""

    def __init__(self, app, *, path_prefix: str = "") -> None:
        super().__init__(app)
        self.path_prefix = path_prefix.rstrip("/")

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)
        try:
            await self._maybe_record(request, response)
        except Exception:
            # Never break the response for telemetry failures.
            pass
        return response

    async def _maybe_record(self, request: Request, response: Response) -> None:
        if request.method.upper() != "GET" or response.status_code >= 400:
            return
        session = request.scope.get("session")
        if not isinstance(session, dict):
            return
        user_id = _uuid_or_none(session.get(SESSION_USER_KEY))
        anon_id = _uuid_or_none(session.get(SESSION_ANON_KEY))
        if user_id is None and anon_id is None:
            return
        db = getattr(request.app.state, "db", None)
        if db is None:
            return
        async with db.session_maker() as db_session:
            svc = ShellSessionService(db_session)
            await svc.classify_and_record_http(
                user_id=user_id,
                anon_id=anon_id if user_id is None else None,
                method=request.method,
                path=request.url.path,
                status=response.status_code,
                path_prefix=self.path_prefix,
            )


def _uuid_or_none(raw: object) -> UUID | None:
    if raw is None:
        return None
    try:
        return UUID(str(raw))
    except ValueError:
        return None
