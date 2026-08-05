"""Federation peer CRUD (mixin for FederationRegistry)."""
from __future__ import annotations

from uuid import UUID

from sqlmodel import col, select

from pymergetic.metal.cdn.models import utcnow
from pymergetic.metal.cdn.services.federation.tables import (
    FederationCredential,
    FederationMount,
    FederationPeer,
    FederationPeerCreate,
    FederationPeerRead,
    FederationPeerUpdate,
)


class PeerOpsMixin:
    async def create_peer(
        self, data: FederationPeerCreate, *, actor_id: UUID | None
    ) -> FederationPeerRead:
        row = FederationPeer(
            label=data.label.strip(),
            base_url=data.base_url,
            public_browse_url=data.public_browse_url,
            status=data.status,
            created_by=actor_id,
        )
        self._session.add(row)
        await self._session.commit()
        await self._session.refresh(row)
        return FederationPeerRead.model_validate(row.model_dump())

    async def list_peers(self) -> list[FederationPeerRead]:
        result = await self._session.exec(
            select(FederationPeer).order_by(col(FederationPeer.label))
        )
        return [FederationPeerRead.model_validate(r.model_dump()) for r in result.all()]

    async def get_peer(self, peer_id: UUID) -> FederationPeerRead | None:
        row = await self._session.get(FederationPeer, peer_id)
        return FederationPeerRead.model_validate(row.model_dump()) if row else None

    async def update_peer(self, peer_id: UUID, data: FederationPeerUpdate) -> FederationPeerRead:
        row = await self._session.get(FederationPeer, peer_id)
        if row is None:
            raise LookupError("peer not found")
        if data.label is not None:
            row.label = data.label.strip()
        if data.base_url is not None:
            row.base_url = data.base_url
        if data.public_browse_url is not None:
            row.public_browse_url = data.public_browse_url
        if data.status is not None:
            row.status = data.status
        row.updated_at = utcnow()
        self._session.add(row)
        await self._session.commit()
        await self._session.refresh(row)
        return FederationPeerRead.model_validate(row.model_dump())

    async def delete_peer(self, peer_id: UUID) -> None:
        mounts = await self._session.exec(
            select(FederationMount).where(FederationMount.peer_id == peer_id)
        )
        if mounts.first() is not None:
            raise ValueError("peer still has mounts; delete mounts first")
        row = await self._session.get(FederationPeer, peer_id)
        if row is None:
            raise LookupError("peer not found")
        creds = await self._session.exec(
            select(FederationCredential).where(FederationCredential.peer_id == peer_id)
        )
        for c in creds.all():
            await self._session.delete(c)
        await self._session.delete(row)
        await self._session.commit()

    # --- mounts -------------------------------------------------------------
