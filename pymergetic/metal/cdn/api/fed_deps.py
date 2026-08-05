"""FastAPI deps for federation read-proxy."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlmodel.ext.asyncio.session import AsyncSession

from pymergetic.metal.cdn.api.deps import SettingsDep, get_session
from pymergetic.metal.cdn.services.federation.proxy import FederationProxy
from pymergetic.metal.cdn.services.federation.registry import FederationRegistry


def _secrets_key(settings: SettingsDep) -> str:
    return (settings.federation_secrets_key or settings.session_secret or "").strip()


def get_federation_registry(
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: SettingsDep,
) -> FederationRegistry:
    key = _secrets_key(settings) or "dev-federation-unconfigured"
    return FederationRegistry(
        session,
        secrets_key=key,
        max_hops=settings.federation_max_hops,
    )


async def get_federation_proxy(
    request: Request,
    settings: SettingsDep,
) -> AsyncIterator[FederationProxy]:
    existing = getattr(request.app.state, "federation_proxy", None)
    if isinstance(existing, FederationProxy):
        yield existing
        return
    client = getattr(request.app.state, "federation_http_client", None)
    proxy = FederationProxy(
        client=client,
        max_hops=settings.federation_max_hops,
        allow_private_net=settings.federation_allow_private_net,
    )
    try:
        yield proxy
    finally:
        if client is None:
            await proxy.aclose()


FederationRegistryDep = Annotated[FederationRegistry, Depends(get_federation_registry)]
FederationProxyDep = Annotated[FederationProxy, Depends(get_federation_proxy)]
