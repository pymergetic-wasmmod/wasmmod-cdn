"""Federation grant accept/revoke (mixin for FederationRegistry)."""
from __future__ import annotations

from uuid import UUID

from sqlmodel import col, select

from pymergetic.metal.cdn.models import ApiKey, ApiKeyCreate, UserCreate, utcnow
from pymergetic.metal.cdn.services.federation.scopes import SCOPE_FEDERATION_READ
from pymergetic.metal.cdn.services.federation.tables import (
    FederationGrant,
    FederationGrantAccept,
    FederationGrantAccepted,
    FederationGrantRead,
    FederationGrantStatus,
)
from pymergetic.metal.cdn.services.identity import ApiKeyService, UserService

FEDERATION_BOT_EMAIL = "federation-bot@cdn.pymergetic.com"


class GrantOpsMixin:
    async def accept_grant(
        self, data: FederationGrantAccept, *, actor_id: UUID
    ) -> FederationGrantAccepted:
        users = UserService(self._session)
        keys = ApiKeyService(self._session)
        bot = await self._ensure_federation_bot(users)
        created = await keys.create(
            bot.id,
            ApiKeyCreate(name=data.key_name, scopes=[SCOPE_FEDERATION_READ]),
        )
        grant = FederationGrant(
            prefix=data.prefix,
            parent_label=data.parent_label.strip(),
            parent_base_url=data.parent_base_url,
            api_key_id=created.id,
            status=FederationGrantStatus.ACTIVE,
            created_by=actor_id,
        )
        self._session.add(grant)
        await self._session.commit()
        await self._session.refresh(grant)
        base = FederationGrantRead.model_validate(grant.model_dump())
        return FederationGrantAccepted(
            **base.model_dump(),
            api_key=created.key,
            api_key_prefix=created.prefix,
        )

    async def list_grants(self) -> list[FederationGrantRead]:
        result = await self._session.exec(
            select(FederationGrant).order_by(col(FederationGrant.created_at).desc())
        )
        return [FederationGrantRead.model_validate(r.model_dump()) for r in result.all()]

    async def revoke_grant(self, grant_id: UUID) -> FederationGrantRead:
        row = await self._session.get(FederationGrant, grant_id)
        if row is None:
            raise LookupError("grant not found")
        row.status = FederationGrantStatus.REVOKED
        row.revoked_at = utcnow()
        if row.api_key_id is not None:
            key = await self._session.get(ApiKey, row.api_key_id)
            if key is not None and key.revoked_at is None:
                key.revoked_at = utcnow()
                self._session.add(key)
        self._session.add(row)
        await self._session.commit()
        await self._session.refresh(row)
        return FederationGrantRead.model_validate(row.model_dump())

    # --- public / status ----------------------------------------------------
    async def _ensure_federation_bot(self, users: UserService):
        existing = await users.get_by_email(FEDERATION_BOT_EMAIL)
        if existing is not None:
            return existing
        # Random unusable password — auth only via API keys.
        import secrets

        pw = secrets.token_urlsafe(32)
        return await users.create(
            UserCreate(
                email=FEDERATION_BOT_EMAIL,
                display_name="Federation bot",
                password=pw,
            ),
            is_admin=False,
        )
