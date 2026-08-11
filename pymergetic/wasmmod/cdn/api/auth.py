"""Auth, session, and API-key endpoints."""

from __future__ import annotations

from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi import status as http_status

from pymergetic.wasmmod.cdn.api.deps import (
    SESSION_USER_KEY,
    ApiKeyServiceDep,
    CurrentUserDep,
    OptionalUserDep,
    SettingsDep,
    ShellSessionServiceDep,
    UserServiceDep,
)
from pymergetic.wasmmod.cdn.middleware.csrf import ensure_csrf_token
from pymergetic.wasmmod.cdn.models import (
    ApiKeyCreate,
    ApiKeyCreated,
    ApiKeyRead,
    CsrfResponse,
    LoginRequest,
    PasswordChangeRequest,
    TokenRequest,
    UserCreate,
    UserRead,
)
from pymergetic.wasmmod.cdn.services.shell_sessions import SESSION_ANON_KEY

auth_router = APIRouter(prefix="/auth", tags=["auth"])


@auth_router.get("/csrf", response_model=CsrfResponse)
async def csrf_token(request: Request) -> CsrfResponse:
    return CsrfResponse(csrf_token=ensure_csrf_token(request))


@auth_router.post("/register", response_model=UserRead, status_code=http_status.HTTP_201_CREATED)
async def register(
    body: UserCreate,
    users: UserServiceDep,
    settings: SettingsDep,
    actor: OptionalUserDep,
) -> UserRead:
    if not settings.registration_open and (actor is None or not actor.is_admin):
        raise HTTPException(status_code=403, detail="registration closed")
    try:
        is_first = (await users.count()) == 0
        return await users.create(body, is_admin=is_first)
    except ValueError as exc:
        raise HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@auth_router.post("/login", response_model=UserRead)
async def login(
    body: LoginRequest,
    request: Request,
    users: UserServiceDep,
    shells: ShellSessionServiceDep,
) -> UserRead:
    user = await users.authenticate(body.email, body.password)
    if user is None:
        raise HTTPException(status_code=401, detail="invalid credentials")
    raw_anon = request.session.get(SESSION_ANON_KEY)
    if raw_anon:
        try:
            await shells.claim_anon(UUID(str(raw_anon)), user.id)
        except ValueError:
            pass
    request.session[SESSION_USER_KEY] = str(user.id)
    ensure_csrf_token(request)
    return user


@auth_router.post("/token", response_model=ApiKeyCreated)
async def issue_token(
    body: TokenRequest,
    users: UserServiceDep,
    keys: ApiKeyServiceDep,
) -> ApiKeyCreated:
    """Password → API key (for headless CLI / CI)."""
    user = await users.authenticate(body.email, body.password)
    if user is None:
        raise HTTPException(status_code=401, detail="invalid credentials")
    return await keys.create(user.id, ApiKeyCreate(name=body.name))


@auth_router.post("/logout", status_code=http_status.HTTP_204_NO_CONTENT)
async def logout(request: Request) -> None:
    # Drop identity only; mint a fresh anon so post-logout hits do not
    # reattach to previously claimed shell sessions.
    request.session.pop(SESSION_USER_KEY, None)
    request.session[SESSION_ANON_KEY] = str(uuid4())
    ensure_csrf_token(request)


@auth_router.post("/password", response_model=UserRead)
async def change_password(
    body: PasswordChangeRequest,
    user: CurrentUserDep,
    users: UserServiceDep,
) -> UserRead:
    """Set a new password (session or Bearer). Requires the current password."""
    if body.new_password == body.current_password:
        raise HTTPException(status_code=400, detail="new password must differ")
    try:
        return await users.set_password(
            user.id,
            current_password=body.current_password,
            new_password=body.new_password,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@auth_router.get("/me", response_model=UserRead)
async def me(user: CurrentUserDep) -> UserRead:
    return user


@auth_router.post(
    "/api-keys", response_model=ApiKeyCreated, status_code=http_status.HTTP_201_CREATED
)
async def create_api_key(
    body: ApiKeyCreate,
    user: CurrentUserDep,
    keys: ApiKeyServiceDep,
) -> ApiKeyCreated:
    return await keys.create(user.id, body)


@auth_router.get("/api-keys", response_model=list[ApiKeyRead])
async def list_api_keys(user: CurrentUserDep, keys: ApiKeyServiceDep) -> list[ApiKeyRead]:
    return await keys.list_for_user(user.id)


@auth_router.delete("/api-keys/{key_id}", status_code=http_status.HTTP_204_NO_CONTENT)
async def revoke_api_key(key_id: UUID, user: CurrentUserDep, keys: ApiKeyServiceDep) -> None:
    ok = await keys.revoke(user.id, key_id)
    if not ok:
        raise HTTPException(status_code=404, detail="api key not found")
