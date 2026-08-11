"""HTTP API routers — thin assembly of domain modules."""

from __future__ import annotations

from fastapi import APIRouter

from pymergetic.wasmmod.cdn.api.artifacts import artifacts_router
from pymergetic.wasmmod.cdn.api.auth import auth_router
from pymergetic.wasmmod.cdn.api.extended import (
    admin_router,
    audit_router,
    index_router,
    ops_router,
    orgs_router,
)
from pymergetic.wasmmod.cdn.api.federation import (
    federation_admin_router,
    federation_public_router,
)
from pymergetic.wasmmod.cdn.api.health import health_router
from pymergetic.wasmmod.cdn.api.identity_routes import acl_router, users_router
from pymergetic.wasmmod.cdn.api.packages import packages_router
from pymergetic.wasmmod.cdn.api.publish import publish_router
from pymergetic.wasmmod.cdn.api.sessions import sessions_router

__all__ = ["build_api_router"]


def build_api_router() -> APIRouter:
    api_router = APIRouter()
    api_router.include_router(health_router)
    api_router.include_router(ops_router)
    api_router.include_router(auth_router)
    api_router.include_router(users_router)
    api_router.include_router(acl_router)
    api_router.include_router(orgs_router)
    api_router.include_router(audit_router)
    api_router.include_router(index_router)
    api_router.include_router(admin_router)
    api_router.include_router(federation_admin_router)
    api_router.include_router(federation_public_router)
    api_router.include_router(packages_router)
    api_router.include_router(publish_router)
    api_router.include_router(artifacts_router)
    api_router.include_router(sessions_router)
    return api_router
