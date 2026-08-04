"""Structured request logging + Prometheus-ish /metrics counters."""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections import defaultdict
from collections.abc import Callable
from threading import Lock

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger("metal_cdn.access")

_LOCK = Lock()
_COUNTERS: dict[str, int] = defaultdict(int)


def incr(name: str, n: int = 1) -> None:
    with _LOCK:
        _COUNTERS[name] += n


def metrics_text() -> str:
    with _LOCK:
        lines = [
            "# HELP metal_cdn_requests_total Request counters",
            "# TYPE metal_cdn_requests_total counter",
        ]
        for key, value in sorted(_COUNTERS.items()):
            lines.append(f'metal_cdn_requests_total{{name="{key}"}} {value}')
        return "\n".join(lines) + "\n"


class RequestLogMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, json_logs: bool = False) -> None:
        super().__init__(app)
        self.json_logs = json_logs

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        req_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            elapsed_ms = (time.perf_counter() - start) * 1000
            self._log(request, req_id, 500, elapsed_ms)
            raise
        elapsed_ms = (time.perf_counter() - start) * 1000
        self._log(request, req_id, response.status_code, elapsed_ms)
        response.headers["X-Request-Id"] = req_id
        return response

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
            logger.info("%s %s %s %.1fms id=%s", request.method, path, status, elapsed_ms, req_id)
