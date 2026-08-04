"""Auth, claim, and ACL fundamentals."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from pymergetic.metal.cdn.main import create_app
from pymergetic.metal.cdn.settings import Settings


@pytest.fixture
async def auth_client(tmp_path: Path):
    settings = Settings(
        data_dir=tmp_path / "data",
        storage_root=tmp_path / "packs",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'auth.db'}",
        base_path="/cdn",
        require_auth=True,
        allow_open_registration=True,
        session_secret="test-secret",
        csrf_enabled=False,
        rate_limit_enabled=False,
        debug=False,
    )
    app = create_app(settings)
    transport = ASGITransport(app=app)
    async with (
        AsyncClient(transport=transport, base_url="http://test") as ac,
        app.router.lifespan_context(app),
    ):
        yield ac


@pytest.mark.asyncio
async def test_blank_site_root(auth_client: AsyncClient) -> None:
    r = await auth_client.get("/")
    assert r.status_code == 200
    assert b"Not Found" not in r.content
    assert b"<body></body>" in r.content.replace(b" ", b"")


@pytest.mark.asyncio
async def test_register_login_claim_publish(auth_client: AsyncClient) -> None:
    reg = await auth_client.post(
        "/cdn/auth/register",
        json={
            "email": "dev@example.com",
            "display_name": "Dev",
            "password": "secret123",
        },
    )
    assert reg.status_code == 201, reg.text
    assert reg.json()["is_admin"] is True

    login = await auth_client.post(
        "/cdn/auth/login",
        json={"email": "dev@example.com", "password": "secret123"},
    )
    assert login.status_code == 200

    me = await auth_client.get("/cdn/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == "dev@example.com"

    claim = await auth_client.post("/cdn/packages/hello/claim")
    assert claim.status_code == 200
    assert claim.json()["created"] is True

    key = await auth_client.post("/cdn/auth/api-keys", json={"name": "ci"})
    assert key.status_code == 201
    token = key.json()["key"]
    assert token.startswith("mcdn_")

    meta = {
        "package": "hello",
        "version": "0.1.0",
        "lead": True,
        "pin": True,
        "deps": {},
    }
    files = [("files", ("hello.wasm", b"\x00asm\x01\x00\x00\x00fake", "application/octet-stream"))]
    # Session cookie still present; also prove bearer works on a fresh client path:
    pr = await auth_client.post(
        "/cdn/publish",
        data={"meta": json.dumps(meta)},
        files=files,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert pr.status_code == 201, pr.text

    mine = await auth_client.get("/cdn/packages/mine")
    assert mine.status_code == 200
    assert any(p["package_name"] == "hello" for p in mine.json())

    denied = await auth_client.post(
        "/cdn/auth/register",
        json={"email": "other@example.com", "password": "secret123", "display_name": "O"},
    )
    assert denied.status_code == 201
    # other user cannot claim hello
    await auth_client.post("/cdn/auth/logout")
    await auth_client.post(
        "/cdn/auth/login",
        json={"email": "other@example.com", "password": "secret123"},
    )
    clash = await auth_client.post("/cdn/packages/hello/claim")
    assert clash.status_code == 409
