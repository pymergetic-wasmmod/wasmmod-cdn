"""Federation registry — peers, mounts, credentials, grants."""

from __future__ import annotations

import json

from sqlmodel.ext.asyncio.session import AsyncSession

from pymergetic.metal.cdn.services.federation.prefix import normalize_mount_prefix
from pymergetic.metal.cdn.services.federation.registry_grants import (
    FEDERATION_BOT_EMAIL,
    GrantOpsMixin,
)
from pymergetic.metal.cdn.services.federation.registry_mounts import MountOpsMixin
from pymergetic.metal.cdn.services.federation.registry_peers import PeerOpsMixin
from pymergetic.metal.cdn.services.federation.tables import (
    FederationDirection,
    FederationGrantStatus,
    FederationPeer,
    FederationPeerStatus,
    FederationPublicMount,
    FederationStatus,
)

__all__ = ["FEDERATION_BOT_EMAIL", "FederationRegistry"]


class FederationRegistry(PeerOpsMixin, MountOpsMixin, GrantOpsMixin):
    def __init__(self, session: AsyncSession, *, secrets_key: str, max_hops: int = 8) -> None:
        self._session = session
        self._secrets_key = secrets_key
        self._max_hops = max_hops

    async def public_mounts(self) -> list[FederationPublicMount]:
        out: list[FederationPublicMount] = []
        for m in await self.list_mounts():
            if not m.enabled or m.direction != FederationDirection.PULL:
                continue
            peer = await self._session.get(FederationPeer, m.peer_id)
            if peer is None or peer.status != FederationPeerStatus.ACTIVE:
                continue
            browse = (peer.public_browse_url or peer.base_url).rstrip("/")
            out.append(
                FederationPublicMount(
                    prefix=m.prefix,
                    peer_label=peer.label,
                    peer_browse_url=browse,
                    direction=m.direction,
                )
            )
        return out

    async def status(self) -> FederationStatus:
        peers = await self.list_peers()
        mounts = await self.list_mounts()
        grants = await self.list_grants()
        return FederationStatus(
            peers=len(peers),
            mounts_enabled=sum(1 for m in mounts if m.enabled),
            mounts_total=len(mounts),
            grants_active=sum(1 for g in grants if g.status == FederationGrantStatus.ACTIVE),
            max_hops=self._max_hops,
            proxy_ready=True,
            detail="read proxy: packages + artifacts on local miss",
        )

    def parse_bootstrap_mounts(self, raw: str | None) -> list[dict]:
        """Parse ``METAL_CDN_FEDERATION_MOUNTS_JSON`` (list of dicts)."""
        if not raw or not raw.strip():
            return []
        data = json.loads(raw)
        if not isinstance(data, list):
            raise ValueError("federation_mounts_json must be a JSON list")
        out: list[dict] = []
        for i, item in enumerate(data):
            if not isinstance(item, dict):
                raise ValueError(f"federation_mounts_json[{i}] must be an object")
            prefix = normalize_mount_prefix(str(item.get("prefix", "")))
            url = str(item.get("url") or item.get("base_url") or "").strip().rstrip("/")
            if not url.startswith(("http://", "https://")):
                raise ValueError(f"federation_mounts_json[{i}].url must be http(s)")
            out.append(
                {
                    "prefix": prefix,
                    "url": url,
                    "token": item.get("token"),
                    "label": str(item.get("label") or prefix),
                }
            )
        return out
