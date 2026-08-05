"""Federation admin + public mount list HTTP API."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from pymergetic.metal.cdn.api.deps import AdminUserDep, AuditServiceDep
from pymergetic.metal.cdn.api.fed_deps import FederationRegistryDep
from pymergetic.metal.cdn.services.federation.tables import (
    FederationCredentialSet,
    FederationFedKeyCreated,
    FederationGrantAccept,
    FederationGrantAccepted,
    FederationGrantRead,
    FederationMountCreate,
    FederationMountRead,
    FederationMountUpdate,
    FederationPeerCreate,
    FederationPeerRead,
    FederationPeerUpdate,
    FederationPublicMount,
    FederationStatus,
)

federation_admin_router = APIRouter(prefix="/admin/federation", tags=["federation"])
federation_public_router = APIRouter(prefix="/federation", tags=["federation"])


@federation_admin_router.get("/status", response_model=FederationStatus)
async def federation_status(reg: FederationRegistryDep, admin: AdminUserDep) -> FederationStatus:
    del admin
    return await reg.status()


@federation_admin_router.get("/peers", response_model=list[FederationPeerRead])
async def list_peers(reg: FederationRegistryDep, admin: AdminUserDep) -> list[FederationPeerRead]:
    del admin
    return await reg.list_peers()


@federation_admin_router.post(
    "/peers", response_model=FederationPeerRead, status_code=status.HTTP_201_CREATED
)
async def create_peer(
    body: FederationPeerCreate,
    reg: FederationRegistryDep,
    admin: AdminUserDep,
    audit: AuditServiceDep,
) -> FederationPeerRead:
    try:
        row = await reg.create_peer(body, actor_id=admin.id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await audit.record(
        "fed.peer.create",
        actor_id=admin.id,
        detail=f"{row.label} {row.base_url}",
    )
    return row


@federation_admin_router.patch("/peers/{peer_id}", response_model=FederationPeerRead)
async def update_peer(
    peer_id: UUID,
    body: FederationPeerUpdate,
    reg: FederationRegistryDep,
    admin: AdminUserDep,
    audit: AuditServiceDep,
) -> FederationPeerRead:
    try:
        row = await reg.update_peer(peer_id, body)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await audit.record("fed.peer.update", actor_id=admin.id, detail=str(peer_id))
    return row


@federation_admin_router.delete("/peers/{peer_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_peer(
    peer_id: UUID,
    reg: FederationRegistryDep,
    admin: AdminUserDep,
    audit: AuditServiceDep,
) -> None:
    try:
        await reg.delete_peer(peer_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await audit.record("fed.peer.delete", actor_id=admin.id, detail=str(peer_id))


@federation_admin_router.get("/mounts", response_model=list[FederationMountRead])
async def list_mounts(reg: FederationRegistryDep, admin: AdminUserDep) -> list[FederationMountRead]:
    del admin
    return await reg.list_mounts()


@federation_admin_router.post(
    "/mounts", response_model=FederationMountRead, status_code=status.HTTP_201_CREATED
)
async def create_mount(
    body: FederationMountCreate,
    reg: FederationRegistryDep,
    admin: AdminUserDep,
    audit: AuditServiceDep,
) -> FederationMountRead:
    try:
        row = await reg.create_mount(body)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await audit.record(
        "fed.mount.create",
        actor_id=admin.id,
        package_name=row.prefix,
        detail=f"peer={row.peer_id}",
    )
    return row


@federation_admin_router.patch("/mounts/{mount_id}", response_model=FederationMountRead)
async def update_mount(
    mount_id: UUID,
    body: FederationMountUpdate,
    reg: FederationRegistryDep,
    admin: AdminUserDep,
    audit: AuditServiceDep,
) -> FederationMountRead:
    try:
        row = await reg.update_mount(mount_id, body)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await audit.record(
        "fed.mount.update",
        actor_id=admin.id,
        package_name=row.prefix,
        detail=str(mount_id),
    )
    return row


@federation_admin_router.delete("/mounts/{mount_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_mount(
    mount_id: UUID,
    reg: FederationRegistryDep,
    admin: AdminUserDep,
    audit: AuditServiceDep,
) -> None:
    try:
        await reg.delete_mount(mount_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await audit.record("fed.mount.delete", actor_id=admin.id, detail=str(mount_id))


@federation_admin_router.put("/mounts/{mount_id}/credential", response_model=FederationMountRead)
async def set_mount_credential(
    mount_id: UUID,
    body: FederationCredentialSet,
    reg: FederationRegistryDep,
    admin: AdminUserDep,
    audit: AuditServiceDep,
) -> FederationMountRead:
    mounts = await reg.list_mounts()
    mount = next((m for m in mounts if m.id == mount_id), None)
    if mount is None:
        raise HTTPException(status_code=404, detail="mount not found")
    try:
        row = await reg.set_credential(peer_id=mount.peer_id, mount_id=mount_id, body=body)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    assert isinstance(row, FederationMountRead)
    await audit.record(
        "fed.credential.rotate",
        actor_id=admin.id,
        package_name=row.prefix,
        detail=row.credential_fingerprint or "",
    )
    return row


@federation_admin_router.post(
    "/mounts/{mount_id}/fed-key",
    response_model=FederationFedKeyCreated,
    status_code=status.HTTP_201_CREATED,
)
async def install_mount_fed_key(
    mount_id: UUID,
    reg: FederationRegistryDep,
    admin: AdminUserDep,
    audit: AuditServiceDep,
) -> FederationFedKeyCreated:
    """Generate Ed25519 keypair, store private on mount, return public key for the child."""
    try:
        row = await reg.install_fed_key(mount_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await audit.record(
        "fed.credential.fed_key",
        actor_id=admin.id,
        detail=f"{mount_id} kid={row.key_id}",
    )
    return row


@federation_admin_router.get("/grants", response_model=list[FederationGrantRead])
async def list_grants(reg: FederationRegistryDep, admin: AdminUserDep) -> list[FederationGrantRead]:
    del admin
    return await reg.list_grants()


@federation_admin_router.post(
    "/grants/accept",
    response_model=FederationGrantAccepted,
    status_code=status.HTTP_201_CREATED,
)
async def accept_grant(
    body: FederationGrantAccept,
    reg: FederationRegistryDep,
    admin: AdminUserDep,
    audit: AuditServiceDep,
) -> FederationGrantAccepted:
    try:
        row = await reg.accept_grant(body, actor_id=admin.id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await audit.record(
        "fed.grant.accept",
        actor_id=admin.id,
        package_name=row.prefix,
        detail=row.parent_label,
    )
    return row


@federation_admin_router.post("/grants/{grant_id}/revoke", response_model=FederationGrantRead)
async def revoke_grant(
    grant_id: UUID,
    reg: FederationRegistryDep,
    admin: AdminUserDep,
    audit: AuditServiceDep,
) -> FederationGrantRead:
    try:
        row = await reg.revoke_grant(grant_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await audit.record(
        "fed.grant.revoke",
        actor_id=admin.id,
        package_name=row.prefix,
        detail=str(grant_id),
    )
    return row


@federation_public_router.get("/mounts", response_model=list[FederationPublicMount])
async def public_mounts(reg: FederationRegistryDep) -> list[FederationPublicMount]:
    """Enabled pull mounts (prefix + browse URL) — no secrets."""
    return await reg.public_mounts()
