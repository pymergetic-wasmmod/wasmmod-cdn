"""FastAPI dependency injection."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated, cast
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlmodel.ext.asyncio.session import AsyncSession

from pymergetic.metal.cdn.db import Database
from pymergetic.metal.cdn.models import UserRead
from pymergetic.metal.cdn.services.audit import AuditService
from pymergetic.metal.cdn.services.channel import IndexService, PublishService
from pymergetic.metal.cdn.services.federation.scopes import scopes_permit_request
from pymergetic.metal.cdn.services.federation.ticket_auth import resolve_metalfed
from pymergetic.metal.cdn.services.identity import AclService, ApiKeyService, UserService
from pymergetic.metal.cdn.services.orgs import OrgService
from pymergetic.metal.cdn.services.shell_sessions import ShellSessionService
from pymergetic.metal.cdn.services.trust import TrustService
from pymergetic.metal.cdn.settings import Settings
from pymergetic.metal.cdn.storage import ObjectStorage
from pymergetic.metal.cdn_client.verify import RequireSignedMode

_bearer = HTTPBearer(auto_error=False)
SESSION_USER_KEY = "user_id"
# Request.state key: scopes for the Bearer API key used on this request (None = session/none).
API_KEY_SCOPES_STATE = "api_key_scopes"
API_KEY_ID_STATE = "api_key_id"
FED_TICKET_PREFIX_STATE = "fed_ticket_prefix"


def get_settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def get_db(request: Request) -> Database:
    return cast(Database, request.app.state.db)


def get_storage(request: Request) -> ObjectStorage:
    return cast(ObjectStorage, request.app.state.storage)


async def get_session(db: Annotated[Database, Depends(get_db)]) -> AsyncIterator[AsyncSession]:
    async with db.session_maker() as session:
        yield session


def get_index_service(
    storage: Annotated[ObjectStorage, Depends(get_storage)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> IndexService:
    return IndexService(storage, signing_key=settings.index_signing_key)


def get_publish_service(
    storage: Annotated[ObjectStorage, Depends(get_storage)],
    indexes: Annotated[IndexService, Depends(get_index_service)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> PublishService:
    mode: RequireSignedMode = cast(RequireSignedMode, settings.require_signed)
    return PublishService(
        storage,
        indexes,
        pin_immutable=settings.pin_immutable,
        require_signed=mode,
    )


def get_user_service(session: Annotated[AsyncSession, Depends(get_session)]) -> UserService:
    return UserService(session)


def get_acl_service(session: Annotated[AsyncSession, Depends(get_session)]) -> AclService:
    return AclService(session)


def get_api_key_service(session: Annotated[AsyncSession, Depends(get_session)]) -> ApiKeyService:
    return ApiKeyService(session)


def get_org_service(session: Annotated[AsyncSession, Depends(get_session)]) -> OrgService:
    return OrgService(session)


def get_audit_service(session: Annotated[AsyncSession, Depends(get_session)]) -> AuditService:
    return AuditService(session)


def get_trust_service(session: Annotated[AsyncSession, Depends(get_session)]) -> TrustService:
    return TrustService(session)


def get_shell_session_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ShellSessionService:
    return ShellSessionService(session)


async def get_optional_user(
    request: Request,
    users: Annotated[UserService, Depends(get_user_service)],
    keys: Annotated[ApiKeyService, Depends(get_api_key_service)],
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> UserRead | None:
    request.state.api_key_scopes = None
    setattr(request.state, API_KEY_ID_STATE, None)
    setattr(request.state, FED_TICKET_PREFIX_STATE, None)

    # MetalFed tickets (server→server) — not handled by HTTPBearer.
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.lower().startswith("metalfed "):
        try:
            resolved = await resolve_metalfed(session, authorization=auth_header)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)
            ) from exc
        if resolved is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid federation ticket"
            )
        user, scopes, claims = resolved
        if not scopes_permit_request(
            scopes,
            method=request.method,
            path=request.url.path,
            base_path=settings.base_path,
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="API key scope insufficient for this operation",
            )
        setattr(request.state, API_KEY_SCOPES_STATE, scopes)
        setattr(request.state, FED_TICKET_PREFIX_STATE, claims.prefix)
        return user

    if creds is not None and creds.scheme.lower() == "bearer":
        resolved = await keys.resolve(creds.credentials)
        if resolved is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid API key")
        user_id, scopes, key_id = resolved
        if not scopes_permit_request(
            scopes,
            method=request.method,
            path=request.url.path,
            base_path=settings.base_path,
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="API key scope insufficient for this operation",
            )
        setattr(request.state, API_KEY_SCOPES_STATE, scopes)
        setattr(request.state, API_KEY_ID_STATE, key_id)
        user = await users.get(user_id)
        if user is None or not user.is_active:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="inactive user")
        return user

    raw = request.session.get(SESSION_USER_KEY)
    if raw:
        try:
            user_id = UUID(str(raw))
        except ValueError:
            return None
        user = await users.get(user_id)
        if user is not None and user.is_active:
            return user
    return None


async def get_current_user(
    user: Annotated[UserRead | None, Depends(get_optional_user)],
) -> UserRead:
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="authentication required"
        )
    return user


async def require_user_if_configured(
    settings: Annotated[Settings, Depends(get_settings)],
    user: Annotated[UserRead | None, Depends(get_optional_user)],
) -> UserRead | None:
    if settings.require_auth and user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="authentication required"
        )
    return user


async def require_admin(user: Annotated[UserRead, Depends(get_current_user)]) -> UserRead:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="admin required")
    return user


SettingsDep = Annotated[Settings, Depends(get_settings)]
StorageDep = Annotated[ObjectStorage, Depends(get_storage)]
IndexServiceDep = Annotated[IndexService, Depends(get_index_service)]
PublishServiceDep = Annotated[PublishService, Depends(get_publish_service)]
UserServiceDep = Annotated[UserService, Depends(get_user_service)]
AclServiceDep = Annotated[AclService, Depends(get_acl_service)]
ApiKeyServiceDep = Annotated[ApiKeyService, Depends(get_api_key_service)]
OrgServiceDep = Annotated[OrgService, Depends(get_org_service)]
AuditServiceDep = Annotated[AuditService, Depends(get_audit_service)]
TrustServiceDep = Annotated[TrustService, Depends(get_trust_service)]
ShellSessionServiceDep = Annotated[ShellSessionService, Depends(get_shell_session_service)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]
OptionalUserDep = Annotated[UserRead | None, Depends(get_optional_user)]
CurrentUserDep = Annotated[UserRead, Depends(get_current_user)]
AuthUserDep = Annotated[UserRead | None, Depends(require_user_if_configured)]
AdminUserDep = Annotated[UserRead, Depends(require_admin)]
