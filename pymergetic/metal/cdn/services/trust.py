"""Server-wide trust root CA store."""

from __future__ import annotations

import hashlib
from uuid import UUID

from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from pymergetic.metal.cdn.models import TrustRoot, TrustRootRead


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
