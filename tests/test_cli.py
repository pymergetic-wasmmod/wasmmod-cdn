"""CLI / token endpoint smoke."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from pymergetic.metal.cdn.main import create_app
from pymergetic.metal.cdn.settings import Settings
from pymergetic.metal.cdn_client import CdnClient


@pytest.fixture
async def live(tmp_path: Path):
    settings = Settings(
        data_dir=tmp_path / "data",
        storage_root=tmp_path / "packs",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'cli.db'}",
        base_path="/cdn",
        require_auth=True,
        session_secret="test-secret",
        allow_open_registration=True,
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
async def test_token_publish_flow(live: AsyncClient) -> None:
    email = "cli@example.com"
    password = "secret123"
    reg = await live.post(
        "/cdn/auth/register",
        json={"email": email, "password": password, "display_name": "CLI"},
    )
    assert reg.status_code == 201, reg.text

    tok = await live.post(
        "/cdn/auth/token",
        json={"email": email, "password": password, "name": "cli"},
    )
    assert tok.status_code == 200, tok.text
    token = tok.json()["key"]
    assert token.startswith("mcdn_")

    client = CdnClient("http://example.invalid/cdn", token=token)
    assert client.base_url.endswith("/cdn/")

    meta = {"package": "clipkg", "version": "1.0.0", "lead": True, "pin": True, "deps": {}}
    files = [("files", ("clipkg.wasm", b"\x00asm\x01\x00\x00\x00x", "application/octet-stream"))]
    pub = await live.post(
        "/cdn/publish",
        data={"meta": json.dumps(meta)},
        files=files,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert pub.status_code == 201, pub.text
    assert "lead" in pub.json()["channels"]
