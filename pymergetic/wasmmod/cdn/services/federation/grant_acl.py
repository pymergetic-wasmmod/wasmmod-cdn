"""Federation bot publish rights under active grant prefixes."""

from __future__ import annotations

from uuid import UUID

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from pymergetic.wasmmod.cdn.services.federation.prefix import name_under_prefix
from pymergetic.wasmmod.cdn.services.federation.registry_grants import FEDERATION_BOT_EMAIL
from pymergetic.wasmmod.cdn.services.federation.tables import (
    FederationGrant,
    FederationGrantStatus,
)
from pymergetic.wasmmod.cdn.services.identity import UserService


async def federation_bot_may_publish(
    session: AsyncSession,
    *,
    user_id: UUID,
    package_name: str,
    api_key_id: UUID | None = None,
    ticket_prefix: str | None = None,
) -> bool:
    """True if federation bot may publish ``package_name``.

    - Ticket path: package must sit under the ticket's grant prefix.
    - Bearer path: package under an active grant; if ``api_key_id`` is set, the
      grant's key must match (tighter binding).
    """
    users = UserService(session)
    user = await users.get(user_id)
    if user is None or str(user.email).lower() != FEDERATION_BOT_EMAIL.lower():
        return False
    if ticket_prefix is not None:
        return name_under_prefix(package_name, ticket_prefix)
    result = await session.exec(
        select(FederationGrant).where(FederationGrant.status == FederationGrantStatus.ACTIVE)
    )
    for grant in result.all():
        if not name_under_prefix(package_name, grant.prefix):
            continue
        if api_key_id is not None and grant.api_key_id is not None and grant.api_key_id != api_key_id:
            continue
        return True
    return False
