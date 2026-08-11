"""Web publish page + session/CSRF multipart upload."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from pymergetic.wasmmod.cdn.main import create_app
from pymergetic.wasmmod.cdn.settings import Settings


@pytest.fixture
async def web_client(tmp_path: Path):
    settings = Settings(
        data_dir=tmp_path / "data",
        storage_root=tmp_path / "packs",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'web.db'}",
        base_path="/cdn",
        require_auth=True,
        allow_open_registration=True,
        session_secret="test-secret",
        csrf_enabled=True,
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
async def test_publish_page_renders(web_client: AsyncClient) -> None:
    r = await web_client.get("/cdn/publish")
    assert r.status_code == 200
    assert b"Publish pack" in r.content
    assert b'id="publish-form"' in r.content


@pytest.mark.asyncio
async def test_session_csrf_publish_from_ui(web_client: AsyncClient) -> None:
    reg = await web_client.post(
        "/cdn/auth/register",
        json={"email": "ui@example.com", "password": "secret123", "display_name": "UI"},
    )
    assert reg.status_code == 201, reg.text

    login = await web_client.post(
        "/cdn/auth/login",
        json={"email": "ui@example.com", "password": "secret123"},
    )
    assert login.status_code == 200

    csrf = await web_client.get("/cdn/auth/csrf")
    assert csrf.status_code == 200
    token = csrf.json()["csrf_token"]
    assert token

    claim = await web_client.post(
        "/cdn/packages/hello/claim",
        headers={"X-CSRF-Token": token},
    )
    assert claim.status_code == 200, claim.text

    meta = {
        "package": "hello",
        "version": "0.2.0",
        "lead": True,
        "pin": True,
        "deps": {},
    }
    files = [("files", ("hello.wasm", b"\x00asm\x01\x00\x00\x00fake", "application/octet-stream"))]
    pub = await web_client.post(
        "/cdn/publish",
        data={"meta": json.dumps(meta)},
        files=files,
        headers={"X-CSRF-Token": token},
    )
    assert pub.status_code == 201, pub.text

    pkg = await web_client.get("/cdn/channels/lead/packs/hello")
    assert pkg.status_code == 200
    assert b"0.2.0" in pkg.content
