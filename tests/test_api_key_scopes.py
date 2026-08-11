"""API key scope enforcement (federation:read / federation:publish)."""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from pymergetic.wasmmod.cdn.main import create_app
from pymergetic.wasmmod.cdn.services.federation.scopes import (
    SCOPE_FEDERATION_PUBLISH,
    SCOPE_FEDERATION_READ,
    scopes_permit_request,
)
from pymergetic.wasmmod.cdn.settings import Settings


def test_scopes_permit_matrix() -> None:
    assert scopes_permit_request([], method="POST", path="/cdn/publish", base_path="/cdn")
    assert scopes_permit_request(
        [SCOPE_FEDERATION_READ], method="GET", path="/cdn/packages", base_path="/cdn"
    )
    assert scopes_permit_request(
        [SCOPE_FEDERATION_READ],
        method="GET",
        path="/cdn/artifacts/lead/x.wasm",
        base_path="/cdn",
    )
    assert not scopes_permit_request(
        [SCOPE_FEDERATION_READ], method="POST", path="/cdn/publish", base_path="/cdn"
    )
    assert not scopes_permit_request(
        [SCOPE_FEDERATION_READ],
        method="POST",
        path="/cdn/packages/hello/claim",
        base_path="/cdn",
    )
    assert scopes_permit_request(
        [SCOPE_FEDERATION_PUBLISH], method="POST", path="/cdn/publish", base_path="/cdn"
    )
    assert scopes_permit_request(
        [SCOPE_FEDERATION_PUBLISH], method="GET", path="/cdn/packages", base_path="/cdn"
    )
    assert not scopes_permit_request(
        [SCOPE_FEDERATION_READ], method="GET", path="/cdn/admin/gc", base_path="/cdn"
    )


def _settings(tmp_path: Path, **kwargs: object) -> Settings:
    base: dict = {
        "data_dir": tmp_path / "data",
        "storage_root": tmp_path / "packs",
        "database_url": f"sqlite+aiosqlite:///{tmp_path / 'scope.db'}",
        "base_path": "/cdn",
        "csrf_enabled": False,
        "rate_limit_enabled": False,
        "debug": False,
        "experimental": False,
        "require_auth": True,
        "allow_open_registration": False,
        "bootstrap_admin_email": "admin@cdn.pymergetic.com",
        "bootstrap_admin_password": "x" * 16,
        "session_secret": "test-session-secret-scopes",
    }
    base.update(kwargs)
    return Settings(**base)


@pytest.mark.asyncio
async def test_federation_read_key_cannot_publish(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))
    transport = ASGITransport(app=app)
    async with (
        AsyncClient(transport=transport, base_url="http://test") as client,
        app.router.lifespan_context(app),
    ):
        token = await client.post(
            "/cdn/auth/token",
            json={
                "email": "admin@cdn.pymergetic.com",
                "password": "x" * 16,
                "name": "admin-cli",
            },
        )
        assert token.status_code == 200, token.text
        admin_key = token.json()["key"]
        headers = {"Authorization": f"Bearer {admin_key}"}

        accept = await client.post(
            "/cdn/admin/federation/grants/accept",
            headers=headers,
            json={"prefix": "leaf.demo", "parent_label": "parent"},
        )
        assert accept.status_code == 201, accept.text
        fed_key = accept.json()["api_key"]
        assert fed_key.startswith("mcdn_")
        fed_headers = {"Authorization": f"Bearer {fed_key}"}

        me = await client.get("/cdn/auth/me", headers=fed_headers)
        assert me.status_code == 200, me.text

        pkgs = await client.get("/cdn/packages", headers=fed_headers)
        assert pkgs.status_code == 200, pkgs.text

        claim = await client.post("/cdn/packages/hello/claim", headers=fed_headers)
        assert claim.status_code == 403
        assert "scope" in claim.json()["detail"].lower()

        pub = await client.post(
            "/cdn/publish",
            headers=fed_headers,
            data={"meta": '{"package":"hello","version":"0.1.0","lead":true,"pin":true}'},
            files={"files": ("hello.wasm", b"\x00asm", "application/octet-stream")},
        )
        assert pub.status_code == 403
