"""Structured request logging + Prometheus-ish /metrics counters."""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections import defaultdict
from threading import Lock

from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger("wasmmod_cdn.access")

_LOCK = Lock()
_COUNTERS: dict[str, int] = defaultdict(int)


def incr(name: str, n: int = 1) -> None:
    with _LOCK:
        _COUNTERS[name] += n


def metrics_text() -> str:
    with _LOCK:
        lines = [
            "# HELP wasmmod_cdn_requests_total Request counters",
            "# TYPE wasmmod_cdn_requests_total counter",
        ]
        for key, value in sorted(_COUNTERS.items()):
            lines.append(f'wasmmod_cdn_requests_total{{name="{key}"}} {value}')
    try:
        from pymergetic.wasmmod.cdn.services.naked_cache import active_naked_cache

        cache = active_naked_cache()
        if cache is not None:
            st = cache.stats()
            lines.extend(
                [
                    "# HELP wasmmod_cdn_naked_cache_hits_total Naked decode cache hits",
                    "# TYPE wasmmod_cdn_naked_cache_hits_total counter",
                    f"wasmmod_cdn_naked_cache_hits_total {st.hits}",
                    "# HELP wasmmod_cdn_naked_cache_misses_total Naked decode cache misses",
                    "# TYPE wasmmod_cdn_naked_cache_misses_total counter",
                    f"wasmmod_cdn_naked_cache_misses_total {st.misses}",
                    "# HELP wasmmod_cdn_naked_cache_bytes Cached naked payload bytes",
                    "# TYPE wasmmod_cdn_naked_cache_bytes gauge",
                    f"wasmmod_cdn_naked_cache_bytes {st.bytes}",
                    "# HELP wasmmod_cdn_naked_cache_entries Cached naked payloads",
                    "# TYPE wasmmod_cdn_naked_cache_entries gauge",
                    f"wasmmod_cdn_naked_cache_entries {st.entries}",
                ]
            )
    except Exception:
        pass
    return "\n".join(lines) + "\n"


class RequestLogMiddleware:
    """Pure ASGI access log (avoids BaseHTTPMiddleware session breakage)."""

    def __init__(self, app: ASGIApp, *, json_logs: bool = False) -> None:
        self.app = app
        self.json_logs = json_logs

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)
        req_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        start = time.perf_counter()
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                headers = MutableHeaders(scope=message)
                headers["X-Request-Id"] = req_id
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            elapsed_ms = (time.perf_counter() - start) * 1000
            self._log(request, req_id, 500, elapsed_ms)
            raise
        elapsed_ms = (time.perf_counter() - start) * 1000
        self._log(request, req_id, status_code, elapsed_ms)

    def _log(self, request: Request, req_id: str, status: int, elapsed_ms: float) -> None:
        path = request.url.path
        incr("http_requests")
        incr(f"status_{status}")
        if path.rstrip("/").endswith("/publish"):
            incr("publish")
        if "/auth/" in path:
            incr("auth")
        if self.json_logs:
            logger.info(
                json.dumps(
                    {
                        "request_id": req_id,
                        "method": request.method,
                        "path": path,
                        "status": status,
                        "duration_ms": round(elapsed_ms, 2),
                    }
                )
            )
        else:
            logger.info(
                "%s %s %s %.1fms id=%s", request.method, path, status, elapsed_ms, req_id
            )
