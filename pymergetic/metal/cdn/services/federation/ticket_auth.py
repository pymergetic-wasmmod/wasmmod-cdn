"""Resolve ``Authorization: MetalFed`` against active grants."""

from __future__ import annotations

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from pymergetic.metal.cdn.models import UserRead
from pymergetic.metal.cdn.services.federation.registry_grants import FEDERATION_BOT_EMAIL
from pymergetic.metal.cdn.services.federation.scopes import (
    SCOPE_FEDERATION_READ,
    normalize_scopes,
)
from pymergetic.metal.cdn.services.federation.tables import (
    FederationGrant,
    FederationGrantStatus,
)
from pymergetic.metal.cdn.services.federation.tickets import (
    TicketClaims,
    parse_authorization,
    verify_ticket,
)
from pymergetic.metal.cdn.services.identity import UserService


async def resolve_metalfed(
    session: AsyncSession,
    *,
    authorization: str | None,
) -> tuple[UserRead, list[str], TicketClaims] | None:
    """Return bot user + scopes + claims if a MetalFed ticket verifies, else None.

    Raises ``ValueError`` for a present-but-invalid MetalFed header.
    """
    raw = parse_authorization(authorization)
    if raw is None:
        return None
    result = await session.exec(
        select(FederationGrant).where(
            FederationGrant.status == FederationGrantStatus.ACTIVE,
            FederationGrant.parent_public_key.is_not(None),  # type: ignore[union-attr]
        )
    )
    grants = [g for g in result.all() if g.parent_public_key]
    if not grants:
        raise ValueError("no federation grant accepts tickets")

    last_err: Exception | None = None
    matched: tuple[FederationGrant, TicketClaims] | None = None
    for grant in grants:
        assert grant.parent_public_key is not None
        try:
            claims = verify_ticket(grant.parent_public_key, raw)
        except ValueError as exc:
            last_err = exc
            continue
        if claims.prefix != grant.prefix:
            last_err = ValueError("federation ticket prefix does not match grant")
            continue
        matched = (grant, claims)
        break

    if matched is None:
        raise ValueError(str(last_err) if last_err else "federation ticket rejected")

    _grant, claims = matched
    users = UserService(session)
    bot = await users.get_by_email(FEDERATION_BOT_EMAIL)
    if bot is None or not bot.is_active:
        raise ValueError("federation bot unavailable")
    try:
        scopes = normalize_scopes(claims.scopes) if claims.scopes else [SCOPE_FEDERATION_READ]
    except ValueError:
        scopes = [SCOPE_FEDERATION_READ]
    if not scopes:
        scopes = [SCOPE_FEDERATION_READ]
    return bot, scopes, claims
