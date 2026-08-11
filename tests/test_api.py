"""Async API smoke tests."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from pymergetic.wasmmod.cdn.main import create_app
from pymergetic.wasmmod.cdn.settings import Settings


@pytest.fixture
async def client(tmp_path: Path) -> AsyncIterator[AsyncClient]:
    settings = Settings(
        data_dir=tmp_path / "data",
        storage_root=tmp_path / "packs",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        base_path="/",
        require_auth=False,
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
async def test_health(client: AsyncClient) -> None:
    r = await client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "version" in body
    assert body["experimental"] is True
    assert body["experimental_message"]
    ready = await client.get("/ready")
    assert ready.status_code == 200
    assert ready.json()["database"] == "ok"
    assert ready.json()["experimental"] is True
    st = await client.get("/status")
    assert st.status_code == 200
    assert st.json()["experimental"] is True
    home = await client.get("/", follow_redirects=True)
    assert b"exp-banner" in home.content
    assert b"Experimental" in home.content


@pytest.mark.asyncio
async def test_status_experimental_off(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path / "data",
        storage_root=tmp_path / "packs",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'st.db'}",
        base_path="/",
        experimental=False,
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
        r = await ac.get("/status")
        assert r.status_code == 200
        assert r.json()["experimental"] is False
        assert r.json()["experimental_message"] is None
        home = await ac.get("/", follow_redirects=True)
        assert b"exp-banner" not in home.content
        assert b"Experimental" not in home.content


@pytest.mark.asyncio
async def test_user_and_publish_flow(client: AsyncClient) -> None:
    ur = await client.post(
        "/users",
        json={"email": "dev@example.com", "display_name": "Dev", "password": "secret123"},
    )
    assert ur.status_code == 201
    user = ur.json()

    ar = await client.post(
        "/acl",
        json={
            "package_name": "hello",
            "user_id": user["id"],
            "role": "owner",
        },
    )
    assert ar.status_code == 201

    meta = {
        "package": "hello",
        "version": "0.1.0",
        "lead": True,
        "pin": True,
        "aot_version": 6,
        "deps": {},
        "maintainer_email": "dev@example.com",
        "publisher_user_id": user["id"],
    }
    files = [
        ("files", ("hello.wasm", b"\x00asm\x01\x00\x00\x00fake", "application/octet-stream")),
    ]
    pr = await client.post(
        "/publish",
        data={"meta": json.dumps(meta)},
        files=files,
    )
    assert pr.status_code == 201, pr.text
    result = pr.json()
    assert result["package"] == "hello"
    assert "lead" in result["channels"]
    assert "@0.1.0" in result["channels"]

    lr = await client.get("/packages")
    assert lr.status_code == 200
    names = [p["name"] for p in lr.json()]
    assert "hello" in names

    vr = await client.get("/packages/hello/versions")
    assert vr.status_code == 200
    ver_rows = vr.json()
    channels = {v["channel"] for v in ver_rows}
    assert "lead" in channels
    assert "@0.1.0" in channels
    assert any(v["label"].startswith("lead") for v in ver_rows)

    gr = await client.get("/packages/hello")
    assert gr.status_code == 200
    assert gr.json()["version"] == "0.1.0"

    br = await client.get("/artifacts/lead/hello.wasm")
    assert br.status_code == 200
    assert br.content.startswith(b"\x00asm")
    hr = await client.head("/artifacts/lead/hello.wasm")
    assert hr.status_code == 200
    assert hr.headers.get("content-length") == str(len(br.content))

    home = await client.get("/", follow_redirects=True)
    assert home.status_code == 200
    assert b"hello" in home.content
    assert b"wasmmod-cdn" in home.content
    assert b"Packages" in home.content
    assert b"footer-ver" in home.content
    assert b"footer-health" in home.content
    assert b"footer-links" in home.content
    assert b"github.com/pymergetic-wasmmod/wasmmod-cdn" in home.content
    assert b"pypi.org/project/pymergetic-wasmmod-cdn" in home.content
    assert b"github.com/pymergetic-wasmmod/wasmmod" in home.content
    assert b">ok<" in home.content

    pin = await client.get("/channels/pin/0.1.0")
    assert pin.status_code == 200
    assert b"hello" in pin.content

    pkg = await client.get("/channels/lead/packs/hello")
    assert pkg.status_code == 200
    assert b"hello.wasm" in pkg.content
    assert b"pkg-version-select" in pkg.content or b"lead" in pkg.content

    docs = await client.get("/docs")
    assert docs.status_code == 200
    assert b"swagger-ui" in docs.content
    assert b"swagger-theme.css" in docs.content
    assert b"Packages" in docs.content


@pytest.mark.asyncio
async def test_base_path_prefix(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path / "data",
        storage_root=tmp_path / "packs",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        base_path="/cdn",
        debug=False,
        csrf_enabled=False,
        rate_limit_enabled=False,
    )
    app = create_app(settings)
    transport = ASGITransport(app=app)
    async with (
        AsyncClient(transport=transport, base_url="http://test") as ac,
        app.router.lifespan_context(app),
    ):
        assert (await ac.get("/health")).status_code == 404
        r = await ac.get("/cdn/health")
        assert r.status_code == 200
        home = await ac.get("/cdn/", follow_redirects=True)
        assert home.status_code == 200
        assert b"/cdn/channels/lead" in home.content
        docs = await ac.get("/cdn/docs")
        assert docs.status_code == 200
        assert b"/cdn/openapi.json" in docs.content
