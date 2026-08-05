"""CSRF for cookie-session mutating requests (Bearer API keys exempt)."""

from __future__ import annotations

import secrets

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

CSRF_SESSION_KEY = "csrf_token"
CSRF_HEADER = "x-csrf-token"
SAFE = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


def ensure_csrf_token(request: Request) -> str:
    token = request.session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        request.session[CSRF_SESSION_KEY] = token
    return str(token)


class CsrfMiddleware:
    """Require X-CSRF-Token when a session cookie authenticates the request.

    Pure ASGI (not BaseHTTPMiddleware) so SessionMiddleware can persist
    login cookies. Exempt: safe methods, Authorization Bearer, and auth
    bootstrap endpoints that establish a session (login/register/token).
    """

    def __init__(self, app: ASGIApp, *, path_prefix: str = "", enabled: bool = True) -> None:
        self.app = app
        self.path_prefix = path_prefix.rstrip("/")
        self.enabled = enabled

    def _strip(self, path: str) -> str:
        if self.path_prefix and path.startswith(self.path_prefix):
            return path[len(self.path_prefix) :] or "/"
        return path

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)
        if not self.enabled or request.method.upper() in SAFE:
            await self.app(scope, receive, send)
            return

        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            await self.app(scope, receive, send)
            return

        path = self._strip(request.url.path)
        if path in ("/auth/login", "/auth/register", "/auth/token"):
            await self.app(scope, receive, send)
            return

        session = scope.get("session")
        if not isinstance(session, dict) or not (
            session.get("user_id") or session.get("anon_id")
        ):
            await self.app(scope, receive, send)
            return

        expected = session.get(CSRF_SESSION_KEY)
        got = request.headers.get(CSRF_HEADER)
        if not expected or not got or not secrets.compare_digest(str(expected), got):
            response = JSONResponse(
                {"detail": "CSRF token missing or invalid"}, status_code=403
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)
