"""HTTP client that forwards reads to a child CDN."""

from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

import httpx

from pymergetic.metal.cdn.services.federation.ssrf import validate_peer_url
from pymergetic.metal.cdn.services.federation.tables import FederationMountRead

log = logging.getLogger("metal_cdn.federation")

FED_HOP_HEADER = "X-Metal-Fed-Hop"
FED_TRACE_HEADER = "X-Metal-Fed-Trace"
FED_VERSION_HEADER = "X-Metal-Fed-Version"
FED_ORIGIN_HEADER = "X-Metal-Origin"
FED_MOUNT_HEADER = "X-Metal-Fed-Mount"
FED_VERSION = "1"


class FederationProxyError(Exception):
    def __init__(self, message: str, *, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


class FederationProxy:
    """Forward GET/HEAD to a peer mount (server→server)."""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        max_hops: int = 8,
        allow_private_net: bool = False,
        timeout_s: float = 30.0,
    ) -> None:
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout_s, follow_redirects=False)
        self._max_hops = max_hops
        self._allow_private_net = allow_private_net

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _check_hops(self, incoming_hop: int) -> int:
        if incoming_hop < 0:
            raise FederationProxyError("invalid federation hop", status_code=400)
        nxt = incoming_hop + 1
        if nxt > self._max_hops:
            raise FederationProxyError(
                f"federation hop limit exceeded ({self._max_hops})",
                status_code=502,
            )
        return nxt

    async def forward(
        self,
        *,
        mount: FederationMountRead,
        path: str,
        method: str = "GET",
        bearer: str | None,
        incoming_hop: int = 0,
        trace: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        if not mount.peer_base_url:
            raise FederationProxyError("mount peer has no base_url", status_code=502)
        base = validate_peer_url(
            mount.peer_base_url, allow_private_net=self._allow_private_net
        )
        hop = self._check_hops(incoming_hop)
        # Lab override: loopback/ASGI tests set allow_private_net.
        rel = path if path.startswith("/") else f"/{path}"
        url = f"{base}{rel}"
        headers = {
            FED_HOP_HEADER: str(hop),
            FED_TRACE_HEADER: trace or uuid4().hex,
            FED_VERSION_HEADER: FED_VERSION,
            "Accept": "*/*",
        }
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        try:
            resp = await self._client.request(method.upper(), url, headers=headers, params=params)
        except httpx.HTTPError as exc:
            log.warning("federation forward failed mount=%s url=%s err=%s", mount.prefix, url, exc)
            raise FederationProxyError(f"peer unreachable: {exc}", status_code=502) from exc
        return resp
