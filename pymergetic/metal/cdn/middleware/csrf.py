"""CSRF for cookie-session mutating requests (Bearer API keys exempt)."""

from __future__ import annotations

import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

CSRF_SESSION_KEY = "csrf_token"
CSRF_HEADER = "x-csrf-token"
SAFE = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


def ensure_csrf_token(request: Request) -> str:
    token = request.session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        request.session[CSRF_SESSION_KEY] = token
    return str(token)


class CsrfMiddleware(BaseHTTPMiddleware):
    """Require X-CSRF-Token when a session cookie authenticates the request.

    Exempt: safe methods, Authorization Bearer, and auth bootstrap endpoints
    that establish a session (login/register/token).
    """

    def __init__(self, app: ASGIApp, *, path_prefix: str = "", enabled: bool = True) -> None:
        super().__init__(app)
        self.path_prefix = path_prefix.rstrip("/")
        self.enabled = enabled

    def _strip(self, path: str) -> str:
        if self.path_prefix and path.startswith(self.path_prefix):
            return path[len(self.path_prefix) :] or "/"
        return path

    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[no-untyped-def]
        if not self.enabled or request.method.upper() in SAFE:
            return await call_next(request)

        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            return await call_next(request)

        path = self._strip(request.url.path)
        if path in ("/auth/login", "/auth/register", "/auth/token"):
            return await call_next(request)

        session = request.scope.get("session")
        if not isinstance(session, dict) or not (
            session.get("user_id") or session.get("anon_id")
        ):
            return await call_next(request)

        expected = session.get(CSRF_SESSION_KEY)
        got = request.headers.get(CSRF_HEADER)
        if not expected or not got or not secrets.compare_digest(str(expected), got):
            return JSONResponse({"detail": "CSRF token missing or invalid"}, status_code=403)
        return await call_next(request)
