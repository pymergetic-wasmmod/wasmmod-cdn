"""Trust bundle (MPTB) endpoints.

The active trust bundle is a signed allow/deny sub-CA revocation policy.
Devices fetch it over the same CDN they already use for packs:

  GET /trust/bundle   -> raw MPTB bytes (device wasm.trust_apply)
  GET /trust/bundle   (Accept: application/json) -> metadata
  GET /trust/policy   -> {applied, allow, deny, expires, bundle_sha256}
  PUT /trust/bundle   (admin, multipart) -> rotate the active bundle
  DELETE /trust/bundle (admin) -> clear it (back to allow-any)
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, UploadFile
from fastapi import HTTPException, Request, Response
from fastapi import status as http_status

from pymergetic.wasmmod.cdn.api.deps import (
    AdminUserDep,
    TrustServiceDep,
)
from pymergetic.wasmmod.cdn.models import TrustBundleRead

trust_router = APIRouter(prefix="/trust", tags=["trust"])


def _policy_response(meta: TrustBundleRead | None, blob_sha256: str | None) -> dict:
    if meta is None:
        return {"applied": False, "allow": 0, "deny": 0, "expires": 0, "bundle_sha256": None}
    return {
        "applied": True,
        "allow": meta.n_allow,
        "deny": meta.n_deny,
        "issued": meta.issued,
        "expires": meta.expires,
        "created_at": meta.created_at.isoformat() if meta.created_at else None,
        "bundle_sha256": blob_sha256 or meta.sha256,
    }


@trust_router.get("/bundle")
async def trust_bundle_get(
    request: Request,
    trust: TrustServiceDep,
) -> Response:
    """Fetch the active MPTB.

    Returns the raw bundle as ``application/octet-stream`` for a device's
    ``wasm.trust_apply``. With ``Accept: application/json`` or a ``.json``
    suffix it returns metadata instead of the blob.
    """
    accept = request.headers.get("accept", "")
    want_json = "application/json" in accept or request.url.path.endswith(".json")
    blob = await trust.get_bundle_blob()
    if want_json:
        meta = await trust.get_bundle()
        return Response(
            content=str(_policy_response(meta, blob_sha256=None)),
            media_type="application/json",
        )
    if blob is None:
        raise HTTPException(status_code=404, detail="no trust bundle active")
    return Response(
        content=blob,
        media_type="application/octet-stream",
        headers={
            "Cache-Control": "no-cache, must-revalidate",
            "X-MPTB-Sha256": hashlib_sha256(blob),
        },
    )


@trust_router.get("/bundle.json", response_model=dict)  # type: ignore[type-arg]
async def trust_bundle_meta(trust: TrustServiceDep) -> dict:
    """Metadata of the active bundle (no blob)."""
    meta = await trust.get_bundle()
    return _policy_response(meta, blob_sha256=None)


@trust_router.get("/policy", response_model=dict)  # type: ignore[type-arg]
async def trust_policy(trust: TrustServiceDep) -> dict:
    """Current active allow/deny sub-CA policy summary."""
    blob = await trust.get_bundle_blob()
    meta = await trust.get_bundle()
    return _policy_response(meta, hashlib_sha256(blob) if blob is not None else None)


@trust_router.put(
    "/bundle",
    response_model=None,
    status_code=http_status.HTTP_204_NO_CONTENT,
)
async def trust_bundle_put(
    _admin: AdminUserDep,
    trust: TrustServiceDep,
    bundle: Annotated[UploadFile, File(description="MPTB bytes from wasmmod sign bundle-gen")],
) -> Response:
    """Rotate the active trust bundle (admin)."""
    blob = await bundle.read()
    if not blob:
        raise HTTPException(status_code=400, detail="empty bundle")
    try:
        await trust.set_bundle(blob)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"invalid trust bundle: {exc}") from exc
    return Response(
        status_code=http_status.HTTP_204_NO_CONTENT,
        headers={"X-MPTB-Sha256": hashlib_sha256(blob)},
    )


@trust_router.delete(
    "/bundle",
    response_model=None,
    status_code=http_status.HTTP_204_NO_CONTENT,
)
async def trust_bundle_delete(
    _admin: AdminUserDep,
    trust: TrustServiceDep,
) -> Response:
    """Clear the active trust bundle (admin) — back to allow-any."""
    await trust.clear_bundle()
    return Response(status_code=http_status.HTTP_204_NO_CONTENT)


def hashlib_sha256(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()
