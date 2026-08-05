"""Admin federation browse page access control."""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from pymergetic.metal.cdn.main import create_app
from pymergetic.metal.cdn.settings import Settings


def _settings(tmp_path: Path, **kwargs: object) -> Settings:
    base: dict = {
        "data_dir": tmp_path / "data",
        "storage_root": tmp_path / "packs",
        "database_url": f"sqlite+aiosqlite:///{tmp_path / 'fed-ui.db'}",
        "base_path": "/cdn",
        "csrf_enabled": False,
        "rate_limit_enabled": False,
        "debug": False,
        "experimental": False,
        "require_auth": False,
        "allow_open_registration": True,
        "session_secret": "test-session-secret-for-federation-ui",
    }
    base.update(kwargs)
    return Settings(**base)


@pytest.mark.asyncio
async def test_federation_page_redirects_anonymous(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))
    transport = ASGITransport(app=app)
    async with (
        AsyncClient(transport=transport, base_url="http://test") as client,
        app.router.lifespan_context(app),
    ):
        res = await client.get("/cdn/federation", follow_redirects=False)
    assert res.status_code == 307
    assert "/login" in res.headers.get("location", "")


@pytest.mark.asyncio
async def test_federation_page_ok_for_admin_session(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))
    transport = ASGITransport(app=app)
    async with (
        AsyncClient(transport=transport, base_url="http://test") as client,
        app.router.lifespan_context(app),
    ):
        reg = await client.post(
            "/cdn/auth/register",
            json={
                "email": "admin@cdn.pymergetic.com",
                "display_name": "Admin",
                "password": "secret12345",
            },
        )
        assert reg.status_code == 201, reg.text
        assert reg.json()["is_admin"] is True
        login = await client.post(
            "/cdn/auth/login",
            json={"email": "admin@cdn.pymergetic.com", "password": "secret12345"},
        )
        assert login.status_code == 200, login.text
        res = await client.get("/cdn/federation")
    assert res.status_code == 200
    assert b"Accept grant" in res.content
    assert b"fed-root" in res.content


@pytest.mark.asyncio
async def test_federation_page_forbidden_for_non_admin(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))
    transport = ASGITransport(app=app)
    async with (
        AsyncClient(transport=transport, base_url="http://test") as client,
        app.router.lifespan_context(app),
    ):
        await client.post(
            "/cdn/auth/register",
            json={
                "email": "first@cdn.pymergetic.com",
                "display_name": "First",
                "password": "secret12345",
            },
        )
        await client.post(
            "/cdn/auth/register",
            json={
                "email": "user@cdn.pymergetic.com",
                "display_name": "User",
                "password": "secret12345",
            },
        )
        login = await client.post(
            "/cdn/auth/login",
            json={"email": "user@cdn.pymergetic.com", "password": "secret12345"},
        )
        assert login.status_code == 200
        assert login.json()["is_admin"] is False
        res = await client.get("/cdn/federation")
    assert res.status_code == 403
