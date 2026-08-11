"""Attribute same-origin CDN GETs to the active shell session."""

from __future__ import annotations

from uuid import UUID

from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from pymergetic.wasmmod.cdn.api.deps import SESSION_USER_KEY
from pymergetic.wasmmod.cdn.services.shell_sessions import SESSION_ANON_KEY, ShellSessionService


class ShellHitMiddleware:
    """After successful GET pack/index/autoexec, record a shell_session event.

    Pure ASGI so it does not break SessionMiddleware cookie persistence.
    """

    def __init__(self, app: ASGIApp, *, path_prefix: str = "") -> None:
        self.app = app
        self.path_prefix = path_prefix.rstrip("/")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
            await send(message)

        await self.app(scope, receive, send_wrapper)
        try:
            await self._maybe_record(request, status_code)
        except Exception:
            # Never break the response for telemetry failures.
            pass

    async def _maybe_record(self, request: Request, status_code: int) -> None:
        if request.method.upper() != "GET" or status_code >= 400:
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
                status=status_code,
                path_prefix=self.path_prefix,
            )


def _uuid_or_none(raw: object) -> UUID | None:
    if raw is None:
        return None
    try:
        return UUID(str(raw))
    except ValueError:
        return None
