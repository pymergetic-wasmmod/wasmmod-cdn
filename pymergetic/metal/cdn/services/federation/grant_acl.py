"""Federation bot publish rights under active grant prefixes."""

from __future__ import annotations

from uuid import UUID

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from pymergetic.metal.cdn.services.federation.prefix import name_under_prefix
from pymergetic.metal.cdn.services.federation.registry_grants import FEDERATION_BOT_EMAIL
from pymergetic.metal.cdn.services.federation.tables import (
    FederationGrant,
    FederationGrantStatus,
)
from pymergetic.metal.cdn.services.identity import UserService


async def federation_bot_may_publish(
    session: AsyncSession,
    *,
    user_id: UUID,
    package_name: str,
) -> bool:
    """True if ``user_id`` is the federation bot and ``package_name`` is under an active grant.

    Scoped ``federation:publish`` is enforced separately in auth deps; this binds
    the bot to grant prefixes so it cannot publish arbitrary packages.
    """
    users = UserService(session)
    user = await users.get(user_id)
    if user is None or str(user.email).lower() != FEDERATION_BOT_EMAIL.lower():
        return False
    result = await session.exec(
        select(FederationGrant).where(FederationGrant.status == FederationGrantStatus.ACTIVE)
    )
    for grant in result.all():
        if name_under_prefix(package_name, grant.prefix):
            return True
    return False
