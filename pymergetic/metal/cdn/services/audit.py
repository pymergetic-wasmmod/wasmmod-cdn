"""Append-only audit log."""

from __future__ import annotations

from uuid import UUID

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from pymergetic.metal.cdn.models import AuditEvent, AuditEventRead


class AuditService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        action: str,
        *,
        actor_id: UUID | None = None,
        package_name: str | None = None,
        detail: str = "",
    ) -> AuditEventRead:
        row = AuditEvent(
            actor_id=actor_id,
            action=action,
            package_name=package_name,
            detail=detail[:2000],
        )
        self._session.add(row)
        await self._session.commit()
        await self._session.refresh(row)
        return AuditEventRead.model_validate(row)

    async def list_recent(
        self,
        *,
        package_name: str | None = None,
        limit: int = 100,
    ) -> list[AuditEventRead]:
        stmt = select(AuditEvent).order_by(col(AuditEvent.created_at).desc()).limit(limit)
        if package_name:
            stmt = stmt.where(AuditEvent.package_name == package_name)
        result = await self._session.exec(stmt)
        return [AuditEventRead.model_validate(r) for r in result.all()]
