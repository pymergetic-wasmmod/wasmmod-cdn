"""Server-wide trust root CA store."""

from __future__ import annotations

import hashlib
from uuid import UUID

from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from pymergetic.wasmmod.cdn.models import TrustBundle, TrustBundleRead, TrustRoot, TrustRootRead
from pymergetic.wasmmod.cdn.models.common import utcnow
from pymergetic.wasmmod.cdn_client.trust import SubcaPolicy, parse_mptb

_ACTIVE_BUNDLE_ID = 1


def _subject_from_der(der: bytes) -> str:
    try:
        return x509.load_der_x509_certificate(der).subject.rfc4514_string()
    except Exception:
        return ""


def normalize_ca_blob(data: bytes) -> bytes:
    """Accept PEM or DER; return DER bytes."""
    if b"BEGIN CERTIFICATE" in data:
        return x509.load_pem_x509_certificate(data).public_bytes(Encoding.DER)
    return x509.load_der_x509_certificate(data).public_bytes(Encoding.DER)


class TrustService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_roots(self) -> list[TrustRootRead]:
        result = await self._session.exec(select(TrustRoot).order_by(col(TrustRoot.created_at)))
        return [
            TrustRootRead(
                id=r.id, name=r.name, sha256=r.sha256, subject=r.subject, created_at=r.created_at
            )
            for r in result.all()
        ]

    async def all_der(self) -> list[bytes]:
        result = await self._session.exec(select(TrustRoot))
        return [r.der for r in result.all()]

    async def add(
        self,
        data: bytes,
        *,
        name: str = "",
        created_by: UUID | None = None,
    ) -> TrustRootRead:
        der = normalize_ca_blob(data)
        digest = hashlib.sha256(der).hexdigest()
        existing = (
            await self._session.exec(select(TrustRoot).where(TrustRoot.sha256 == digest))
        ).first()
        if existing is not None:
            return TrustRootRead(
                id=existing.id,
                name=existing.name,
                sha256=existing.sha256,
                subject=existing.subject,
                created_at=existing.created_at,
            )
        row = TrustRoot(
            name=name or digest[:12],
            sha256=digest,
            der=der,
            subject=_subject_from_der(der),
            created_by=created_by,
        )
        self._session.add(row)
        await self._session.commit()
        await self._session.refresh(row)
        return TrustRootRead(
            id=row.id, name=row.name, sha256=row.sha256, subject=row.subject, created_at=row.created_at
        )

    async def delete(self, root_id: UUID) -> bool:
        row = await self._session.get(TrustRoot, root_id)
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.commit()
        return True

    # ------------------------------------------------------------------
    # Trust bundle (MPTB) — the active allow/deny sub-CA revocation policy.
    # ------------------------------------------------------------------

    async def get_bundle(self) -> TrustBundleRead | None:
        """Metadata of the active bundle, or None if none has been set."""
        row = await self._session.get(TrustBundle, _ACTIVE_BUNDLE_ID)
        if row is None:
            return None
        return TrustBundleRead(
            sha256=row.sha256,
            issued=row.issued,
            expires=row.expires,
            n_allow=row.n_allow,
            n_deny=row.n_deny,
            created_at=row.created_at,
        )

    async def get_bundle_blob(self) -> bytes | None:
        """Raw bytes of the active bundle (verbatim, for device trust_apply)."""
        row = await self._session.get(TrustBundle, _ACTIVE_BUNDLE_ID)
        return row.blob if row is not None else None

    async def set_bundle(self, blob: bytes) -> TrustBundleRead:
        """Rotate the active bundle.

        The blob is parsed (not crypto-authenticated here — that happens at
        apply on a trust-root-holding device/CDN). Last-writer-wins on the
        fixed row id so GET always returns one deterministic policy.
        """
        parsed = parse_mptb(blob)
        existing = await self._session.get(TrustBundle, _ACTIVE_BUNDLE_ID)
        now = _unix_now()
        digest = hashlib.sha256(blob).hexdigest()
        if existing is None:
            existing = TrustBundle(
                id=_ACTIVE_BUNDLE_ID,
                blob=blob,
                sha256=digest,
                issued=parsed.issued,
                expires=parsed.expires,
                n_allow=len(parsed.allow),
                n_deny=len(parsed.deny),
                created_at=utcnow(),
            )
            self._session.add(existing)
        else:
            existing.blob = blob
            existing.sha256 = digest
            existing.issued = parsed.issued
            existing.expires = parsed.expires
            existing.n_allow = len(parsed.allow)
            existing.n_deny = len(parsed.deny)
            existing.created_at = utcnow()
        await self._session.commit()
        await self._session.refresh(existing)
        return TrustBundleRead(
            sha256=existing.sha256,
            issued=existing.issued,
            expires=existing.expires,
            n_allow=existing.n_allow,
            n_deny=existing.n_deny,
            created_at=existing.created_at,
        )

    async def clear_bundle(self) -> bool:
        row = await self._session.get(TrustBundle, _ACTIVE_BUNDLE_ID)
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.commit()
        return True

    async def active_policy(self, *, now: int | None = None) -> SubcaPolicy:
        """The allow/deny sub-CA policy an enforcing publisher should apply.

        Empty policy (no active bundle) permits every trusted root — identity
        only, matching the device's no-policy mode. An expired active bundle
        revokes *nothing* (fails open to identity-only) so a lapsed CDN cannot
        silently hard-block all publishing; devices are still free to reject
        expiry on their side.
        """
        row = await self._session.get(TrustBundle, _ACTIVE_BUNDLE_ID)
        if row is None:
            return SubcaPolicy()
        parsed = parse_mptb(row.blob)
        if parsed.is_expired(now):
            return SubcaPolicy()
        return SubcaPolicy(allow=parsed.allow, deny=parsed.deny)


def _unix_now() -> int:
    import time

    return int(time.time())
