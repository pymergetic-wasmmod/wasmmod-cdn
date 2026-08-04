"""Promote, yank, pin immutability, ETag, search, CSRF, registration defaults."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from pymergetic.metal.cdn.main import create_app
from pymergetic.metal.cdn.settings import Settings


@pytest.fixture
async def client(tmp_path: Path):
    settings = Settings(
        data_dir=tmp_path / "data",
        storage_root=tmp_path / "packs",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'life.db'}",
        base_path="/",
        require_auth=True,
        allow_open_registration=True,
        csrf_enabled=False,
        rate_limit_enabled=False,
        pin_immutable=True,
        session_secret="test-secret",
        debug=False,
    )
    app = create_app(settings)
    transport = ASGITransport(app=app)
    async with (
        AsyncClient(transport=transport, base_url="http://test") as ac,
        app.router.lifespan_context(app),
    ):
        await ac.post(
            "/auth/register",
            json={"email": "dev@example.com", "password": "secret123", "display_name": "Dev"},
        )
        tok = await ac.post(
            "/auth/token",
            json={"email": "dev@example.com", "password": "secret123", "name": "ci"},
        )
        ac.headers["Authorization"] = f"Bearer {tok.json()['key']}"
        yield ac


async def _publish(ac: AsyncClient, package: str, version: str, **extra: object) -> dict:
    meta = {
        "package": package,
        "version": version,
        "lead": True,
        "pin": True,
        "deps": {},
        "description": f"{package} demo",
        **extra,
    }
    files = [
        ("files", (f"{package}.wasm", b"\x00asm\x01\x00\x00\x00x", "application/octet-stream"))
    ]
    r = await ac.post("/publish", data={"meta": json.dumps(meta)}, files=files)
    assert r.status_code == 201, r.text
    return r.json()


@pytest.mark.asyncio
async def test_pin_immutable_and_force(client: AsyncClient) -> None:
    await _publish(client, "hello", "1.0.0")
    meta = {"package": "hello", "version": "1.0.0", "lead": False, "pin": True, "deps": {}}
    files = [("files", ("hello.wasm", b"\x00asm\x01\x00\x00\x00y", "application/octet-stream"))]
    denied = await client.post("/publish", data={"meta": json.dumps(meta)}, files=files)
    assert denied.status_code == 409
    meta["force"] = True
    forced = await client.post("/publish", data={"meta": json.dumps(meta)}, files=files)
    assert forced.status_code == 201, forced.text


@pytest.mark.asyncio
async def test_promote_yank_search_etag(client: AsyncClient) -> None:
    await _publish(client, "mixed", "0.2.0")
    # republish pin-only newer then promote older? promote 0.2.0 to lead (already there)
    # publish pin-only 0.3.0 without lead
    meta = {
        "package": "mixed",
        "version": "0.3.0",
        "lead": False,
        "pin": True,
        "deps": {},
        "description": "mixed next",
    }
    files = [("files", ("mixed.wasm", b"\x00asm\x01\x00\x00\x00z", "application/octet-stream"))]
    assert (
        await client.post("/publish", data={"meta": json.dumps(meta)}, files=files)
    ).status_code == 201

    prom = await client.post("/packages/mixed/promote", json={"version": "0.3.0"})
    assert prom.status_code == 200, prom.text
    lead = await client.get("/packages/mixed")
    assert lead.json()["version"] == "0.3.0"

    yank = await client.post(
        "/packages/mixed/yank",
        json={"reason": "bad build", "channel": "lead"},
    )
    assert yank.status_code == 200
    assert yank.json()["yanked"] is True

    search = await client.get("/packages/search", params={"q": "mixed", "include_yanked": "true"})
    assert search.status_code == 200
    assert any(p["name"] == "mixed" for p in search.json())

    art = await client.get("/artifacts/pin/0.3.0/mixed.wasm")
    assert art.status_code == 200
    assert "ETag" in art.headers
    etag = art.headers["ETag"]
    cached = await client.get(
        "/artifacts/pin/0.3.0/mixed.wasm",
        headers={"If-None-Match": etag},
    )
    assert cached.status_code == 304


@pytest.mark.asyncio
async def test_csrf_blocks_cookie_mutation(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path / "data",
        storage_root=tmp_path / "packs",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'csrf.db'}",
        base_path="/",
        require_auth=False,
        allow_open_registration=True,
        csrf_enabled=True,
        rate_limit_enabled=False,
        session_secret="csrf-secret",
        debug=False,
    )
    app = create_app(settings)
    transport = ASGITransport(app=app)
    async with (
        AsyncClient(transport=transport, base_url="http://test") as ac,
        app.router.lifespan_context(app),
    ):
        await ac.post(
            "/auth/register",
            json={"email": "a@example.com", "password": "secret123", "display_name": "A"},
        )
        await ac.post(
            "/auth/login",
            json={"email": "a@example.com", "password": "secret123"},
        )
        blocked = await ac.post("/packages/x/claim")
        assert blocked.status_code == 403
        csrf = await ac.get("/auth/csrf")
        token = csrf.json()["csrf_token"]
        ok = await ac.post("/packages/x/claim", headers={"X-CSRF-Token": token})
        assert ok.status_code == 200, ok.text


@pytest.mark.asyncio
async def test_require_auth_closes_registration_by_default(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path / "data",
        storage_root=tmp_path / "packs",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'reg.db'}",
        base_path="/",
        require_auth=True,
        # allow_open_registration left unset → False
        csrf_enabled=False,
        rate_limit_enabled=False,
        session_secret="x",
        debug=False,
    )
    assert settings.registration_open is False
    app = create_app(settings)
    transport = ASGITransport(app=app)
    async with (
        AsyncClient(transport=transport, base_url="http://test") as ac,
        app.router.lifespan_context(app),
    ):
        r = await ac.post(
            "/auth/register",
            json={"email": "nope@example.com", "password": "secret123"},
        )
        assert r.status_code == 403
