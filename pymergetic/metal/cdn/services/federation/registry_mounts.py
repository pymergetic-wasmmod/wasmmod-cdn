"""Federation mount + credential ops (mixin for FederationRegistry)."""
from __future__ import annotations

from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select

from pymergetic.metal.cdn.models import utcnow
from pymergetic.metal.cdn.services.federation.prefix import (
    longest_prefix_mount,
)
from pymergetic.metal.cdn.services.federation.secrets import (
    decrypt_secret,
    encrypt_secret,
    secret_fingerprint,
)
from pymergetic.metal.cdn.services.federation.tables import (
    FederationCredential,
    FederationCredentialSet,
    FederationCredKind,
    FederationDirection,
    FederationFedKeyCreated,
    FederationMount,
    FederationMountCreate,
    FederationMountRead,
    FederationMountUpdate,
    FederationPeer,
    FederationPeerRead,
)


class MountOpsMixin:
    async def create_mount(self, data: FederationMountCreate) -> FederationMountRead:
        peer = await self._session.get(FederationPeer, data.peer_id)
        if peer is None:
            raise LookupError("peer not found")
        row = FederationMount(
            prefix=data.prefix,
            peer_id=data.peer_id,
            direction=data.direction,
            shadow_policy=data.shadow_policy,
            max_hops_override=data.max_hops_override,
            enabled=data.enabled,
            notes=data.notes or "",
        )
        self._session.add(row)
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise ValueError(f"mount prefix already exists: {data.prefix}") from exc
        await self._session.refresh(row)
        if data.bearer_token:
            await self.set_credential(
                peer_id=row.peer_id,
                mount_id=row.id,
                body=FederationCredentialSet(bearer_token=data.bearer_token),
            )
        elif data.fed_private_key:
            await self.set_credential(
                peer_id=row.peer_id,
                mount_id=row.id,
                body=FederationCredentialSet(fed_private_key=data.fed_private_key),
            )
        return await self._mount_read(row)

    async def list_mounts(self) -> list[FederationMountRead]:
        result = await self._session.exec(
            select(FederationMount).order_by(col(FederationMount.prefix))
        )
        out: list[FederationMountRead] = []
        for row in result.all():
            out.append(await self._mount_read(row))
        return out

    async def update_mount(
        self, mount_id: UUID, data: FederationMountUpdate
    ) -> FederationMountRead:
        row = await self._session.get(FederationMount, mount_id)
        if row is None:
            raise LookupError("mount not found")
        if data.peer_id is not None:
            peer = await self._session.get(FederationPeer, data.peer_id)
            if peer is None:
                raise LookupError("peer not found")
            row.peer_id = data.peer_id
        if data.direction is not None:
            row.direction = data.direction
        if data.shadow_policy is not None:
            row.shadow_policy = data.shadow_policy
        if data.max_hops_override is not None:
            row.max_hops_override = data.max_hops_override
        if data.enabled is not None:
            row.enabled = data.enabled
        if data.notes is not None:
            row.notes = data.notes
        row.updated_at = utcnow()
        self._session.add(row)
        await self._session.commit()
        await self._session.refresh(row)
        return await self._mount_read(row)

    async def delete_mount(self, mount_id: UUID) -> None:
        row = await self._session.get(FederationMount, mount_id)
        if row is None:
            raise LookupError("mount not found")
        creds = await self._session.exec(
            select(FederationCredential).where(FederationCredential.mount_id == mount_id)
        )
        for c in creds.all():
            await self._session.delete(c)
        await self._session.delete(row)
        await self._session.commit()

    async def resolve_mount_for_package(
        self,
        package_name: str,
        *,
        direction: FederationDirection = FederationDirection.PULL,
    ) -> FederationMountRead | None:
        mounts = await self.list_mounts()
        enabled = [
            (m.prefix, m)
            for m in mounts
            if m.enabled and m.direction == direction
        ]
        hit = longest_prefix_mount(package_name, enabled)
        return hit[1] if hit else None

    # --- credentials --------------------------------------------------------

    async def set_credential(
        self,
        *,
        peer_id: UUID,
        mount_id: UUID | None,
        body: FederationCredentialSet,
    ) -> FederationMountRead | FederationPeerRead:
        peer = await self._session.get(FederationPeer, peer_id)
        if peer is None:
            raise LookupError("peer not found")
        if mount_id is not None:
            mount = await self._session.get(FederationMount, mount_id)
            if mount is None or mount.peer_id != peer_id:
                raise LookupError("mount not found for peer")
        # Replace existing credential for this peer/mount pair.
        stmt = select(FederationCredential).where(FederationCredential.peer_id == peer_id)
        if mount_id is None:
            stmt = stmt.where(FederationCredential.mount_id.is_(None))  # type: ignore[union-attr]
        else:
            stmt = stmt.where(FederationCredential.mount_id == mount_id)
        existing = (await self._session.exec(stmt)).all()
        for c in existing:
            await self._session.delete(c)
        try:
            material, kind = body.material_and_kind()
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        kid = body.key_id or ""
        if kind == FederationCredKind.FED_KEY:
            from pymergetic.metal.cdn.services.federation.tickets import public_from_private

            try:
                _pub, derived_kid = public_from_private(material)
            except Exception as exc:
                raise ValueError(f"invalid fed_private_key: {exc}") from exc
            kid = kid or derived_kid
        fp = secret_fingerprint(material)
        row = FederationCredential(
            peer_id=peer_id,
            mount_id=mount_id,
            kind=kind,
            ciphertext=encrypt_secret(material, secret_key=self._secrets_key),
            fingerprint=fp,
            key_id=kid,
        )
        self._session.add(row)
        await self._session.commit()
        if mount_id is not None:
            mount = await self._session.get(FederationMount, mount_id)
            assert mount is not None
            return await self._mount_read(mount)
        return FederationPeerRead.model_validate(peer.model_dump())

    async def _cred_for_mount(self, mount_id: UUID) -> FederationCredential | None:
        mount = await self._session.get(FederationMount, mount_id)
        if mount is None:
            return None
        mount_cred = (
            await self._session.exec(
                select(FederationCredential).where(
                    FederationCredential.peer_id == mount.peer_id,
                    FederationCredential.mount_id == mount_id,
                )
            )
        ).first()
        if mount_cred is not None:
            return mount_cred
        return (
            await self._session.exec(
                select(FederationCredential).where(
                    FederationCredential.peer_id == mount.peer_id,
                    FederationCredential.mount_id.is_(None),  # type: ignore[union-attr]
                )
            )
        ).first()

    async def get_bearer_for_mount(self, mount_id: UUID) -> str | None:
        """Decrypt bearer for proxy use (mount-specific, else peer-level)."""
        cred = await self._cred_for_mount(mount_id)
        if cred is None or cred.kind != FederationCredKind.BEARER:
            return None
        return decrypt_secret(cred.ciphertext, secret_key=self._secrets_key)

    async def get_fed_private_for_mount(self, mount_id: UUID) -> tuple[str, str] | None:
        """Return ``(private_b64, key_id)`` when mount has an Ed25519 credential."""
        cred = await self._cred_for_mount(mount_id)
        if cred is None or cred.kind != FederationCredKind.FED_KEY:
            return None
        material = decrypt_secret(cred.ciphertext, secret_key=self._secrets_key)
        return material, cred.key_id or ""

    async def install_fed_key(self, mount_id: UUID) -> FederationFedKeyCreated:
        """Generate Ed25519 keypair, store private on mount, return public (+ kid)."""
        from pymergetic.metal.cdn.services.federation.tickets import generate_keypair

        mount = await self._session.get(FederationMount, mount_id)
        if mount is None:
            raise LookupError("mount not found")
        pair = generate_keypair()
        await self.set_credential(
            peer_id=mount.peer_id,
            mount_id=mount_id,
            body=FederationCredentialSet(
                fed_private_key=pair.private_b64,
                key_id=pair.key_id,
            ),
        )
        return FederationFedKeyCreated(
            mount_id=mount_id,
            public_key=pair.public_b64,
            key_id=pair.key_id,
        )

    # --- grants (child accept) ----------------------------------------------
    async def _mount_read(self, row: FederationMount) -> FederationMountRead:
        peer = await self._session.get(FederationPeer, row.peer_id)
        cred_stmt = select(FederationCredential).where(
            FederationCredential.peer_id == row.peer_id,
            FederationCredential.mount_id == row.id,
        )
        cred = (await self._session.exec(cred_stmt)).first()
        if cred is None:
            peer_cred_stmt = select(FederationCredential).where(
                FederationCredential.peer_id == row.peer_id,
                FederationCredential.mount_id.is_(None),  # type: ignore[union-attr]
            )
            cred = (await self._session.exec(peer_cred_stmt)).first()
        return FederationMountRead(
            id=row.id,
            prefix=row.prefix,
            peer_id=row.peer_id,
            direction=row.direction,
            shadow_policy=row.shadow_policy,
            max_hops_override=row.max_hops_override,
            enabled=row.enabled,
            notes=row.notes,
            created_at=row.created_at,
            updated_at=row.updated_at,
            has_credential=cred is not None,
            credential_fingerprint=cred.fingerprint if cred else None,
            peer_label=peer.label if peer else None,
            peer_base_url=peer.base_url if peer else None,
        )
