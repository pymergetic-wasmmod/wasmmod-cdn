"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from pymergetic.metal.cdn import __version__
from pymergetic.metal.cdn.api import build_api_router
from pymergetic.metal.cdn.db import Database
from pymergetic.metal.cdn.middleware import (
    CsrfMiddleware,
    RateLimitMiddleware,
    RequestLogMiddleware,
    ShellHitMiddleware,
)
from pymergetic.metal.cdn.paths import join_base
from pymergetic.metal.cdn.services.identity import UserService
from pymergetic.metal.cdn.services.naked_cache import (
    install_naked_cache,
    naked_cache_from_settings,
)
from pymergetic.metal.cdn.settings import Settings, get_settings
from pymergetic.metal.cdn.storage import build_storage
from pymergetic.metal.cdn.web.routes import configure_web, web_router

STATIC_DIR = Path(__file__).resolve().parent / "web" / "static"
BLANK_HTML = (
    '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
    "<title></title></head><body></body></html>"
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    settings.ensure_dirs()
    db: Database = app.state.db
    await db.create_all()
    if settings.bootstrap_admin_email and settings.bootstrap_admin_password:
        async with db.session_maker() as session:
            users = UserService(session)
            created = await users.ensure_bootstrap_admin(
                str(settings.bootstrap_admin_email),
                settings.bootstrap_admin_password,
            )
            if created is not None:
                print(f"bootstrapped admin {created.email}")
    if settings.federation_mounts_json:
        secret = (settings.federation_secrets_key or settings.session_secret or "").strip()
        if not secret:
            print("federation bootstrap mounts skipped: no session/federation secrets key")
        else:
            from pymergetic.metal.cdn.services.federation.registry import FederationRegistry

            async with db.session_maker() as session:
                reg = FederationRegistry(
                    session,
                    secrets_key=secret,
                    max_hops=settings.federation_max_hops,
                )
                for line in await reg.apply_bootstrap_mounts(
                    settings.federation_mounts_json,
                    allow_private_net=settings.federation_allow_private_net,
                ):
                    print(f"federation bootstrap: {line}")
    yield
    install_naked_cache(None)
    await db.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    settings.ensure_dirs()
    db = Database(settings)
    storage = build_storage(settings)
    prefix = settings.path_prefix
    openapi_url = join_base(settings.base_path, "openapi.json")

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=openapi_url,
        root_path=settings.root_path,
        description=(
            "CDN for wasmmod packs: channel state in object storage, "
            "publisher identity in SQLModel. Mounted at configurable base_path."
        ),
    )
    app.state.settings = settings
    app.state.db = db
    app.state.storage = storage
    naked_cache = naked_cache_from_settings(settings)
    app.state.naked_cache = naked_cache
    install_naked_cache(naked_cache)

    # Last added = outermost.
    if settings.cors_origins:
        # '*' cannot mix with credentials; cookie UIs need explicit origins.
        allow_creds = settings.cors_origins != ["*"]
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=allow_creds,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=["*"],
        )
    app.add_middleware(ShellHitMiddleware, path_prefix=prefix)
    app.add_middleware(RequestLogMiddleware, json_logs=settings.json_logs)
    app.add_middleware(
        CsrfMiddleware,
        path_prefix=prefix,
        enabled=settings.csrf_enabled,
    )
    if settings.rate_limit_enabled:
        app.add_middleware(
            RateLimitMiddleware,
            path_prefix=prefix,
            login_limit=settings.rate_limit_login,
            publish_limit=settings.rate_limit_publish,
            window_s=settings.rate_limit_window_s,
        )
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        same_site="lax",
        https_only=settings.session_cookie_secure,
    )
    if settings.behind_proxy:
        app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")
    if settings.trusted_hosts != ["*"]:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_hosts)

    if prefix:
        # Host root stays empty when the app lives under /cdn (or similar).
        @app.get("/", include_in_schema=False)
        async def site_root() -> HTMLResponse:
            return HTMLResponse(BLANK_HTML, status_code=200)

        @app.get("/robots.txt", include_in_schema=False)
        async def robots() -> HTMLResponse:
            return HTMLResponse(
                "User-agent: *\nDisallow:\n", status_code=200, media_type="text/plain"
            )

    configure_web(settings.base_path)
    app.include_router(build_api_router(), prefix=prefix)
    app.include_router(web_router, prefix=prefix)
    # Shared Inspect contract (/capabilities, stub /inspect/self); keep CDN /health.
    from pymergetic.metal.cdn.api.inspect_contract import mount_inspect_contract

    mount_inspect_contract(app, prefix=prefix)

    class _ReplAwareStaticFiles(StaticFiles):
        """µPy REPL assets must not be sticky-cached across deploys."""

        async def get_response(self, path: str, scope):  # type: ignore[no-untyped-def]
            response = await super().get_response(path, scope)
            # micropython.mjs/.wasm — autoexec is already no-store; without this the
            # browser can keep an old binary while the banner looks "new".
            if path.startswith(("repl/micropython.", "inspect/", "css/")) or path in (
                "inspect.js",
                "site.css",
                "repl.js",
            ):
                response.headers["Cache-Control"] = "no-cache, must-revalidate"
            # Brand / mark assets: allow other origins to embed (fork UIs).
            if path.startswith("img/"):
                response.headers.setdefault("Cross-Origin-Resource-Policy", "cross-origin")
                response.headers.setdefault("Access-Control-Allow-Origin", "*")
            return response

    app.mount(
        join_base(settings.base_path, "static"),
        _ReplAwareStaticFiles(directory=str(STATIC_DIR)),
        name="static",
    )
    return app


app = create_app()
