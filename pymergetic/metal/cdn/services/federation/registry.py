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
            detail="read proxy + optional PUSH upstream publish",
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
            token = item.get("token")
            out.append(
                {
                    "prefix": prefix,
                    "url": url,
                    "token": str(token).strip() if token else None,
                    "label": str(item.get("label") or prefix),
                }
            )
        return out

    async def apply_bootstrap_mounts(
        self,
        raw: str | None,
        *,
        allow_private_net: bool = False,
    ) -> list[str]:
        """Idempotent peer/mount/credential create from bootstrap JSON.

        Existing mount prefixes win (admin/DB not overwritten). Returns human
        log lines for each action or skip.
        """
        from pymergetic.metal.cdn.services.federation.ssrf import validate_peer_url
        from pymergetic.metal.cdn.services.federation.tables import (
            FederationMountCreate,
            FederationPeerCreate,
            FederationPeerStatus,
        )

        try:
            items = self.parse_bootstrap_mounts(raw)
        except (ValueError, json.JSONDecodeError) as exc:
            return [f"bootstrap mounts skipped: {exc}"]
        if not items:
            return []

        existing = {m.prefix: m for m in await self.list_mounts()}
        peers = await self.list_peers()
        by_url = {p.base_url.rstrip("/"): p for p in peers}
        actions: list[str] = []

        for item in items:
            prefix = item["prefix"]
            if prefix in existing:
                actions.append(f"skip mount {prefix} (already present)")
                continue
            try:
                url = validate_peer_url(item["url"], allow_private_net=allow_private_net)
            except ValueError as exc:
                actions.append(f"skip mount {prefix}: {exc}")
                continue
            peer = by_url.get(url)
            if peer is None:
                peer = await self.create_peer(
                    FederationPeerCreate(
                        label=item["label"],
                        base_url=url,
                        status=FederationPeerStatus.ACTIVE,
                    ),
                    actor_id=None,
                )
                by_url[url] = peer
                actions.append(f"created peer {peer.label} ({url})")
            token = item.get("token")
            if token is not None and len(token) < 8:
                actions.append(f"skip mount {prefix}: token too short")
                continue
            mount = await self.create_mount(
                FederationMountCreate(
                    prefix=prefix,
                    peer_id=peer.id,
                    bearer_token=token,
                    notes="bootstrap:federation_mounts_json",
                )
            )
            existing[prefix] = mount
            actions.append(
                f"created mount {prefix} → {peer.label}"
                + (" (with credential)" if token else "")
            )
        return actions
