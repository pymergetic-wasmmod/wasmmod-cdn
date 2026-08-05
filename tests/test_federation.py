"""Federation P0: prefix match, scopes, registry admin API."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from pymergetic.metal.cdn.main import create_app
from pymergetic.metal.cdn.services.federation.prefix import (
    longest_prefix_mount,
    name_under_prefix,
    normalize_mount_prefix,
)
from pymergetic.metal.cdn.services.federation.scopes import (
    SCOPE_FEDERATION_READ,
    key_allows,
    normalize_scopes,
    scopes_from_storage,
    scopes_to_storage,
)
from pymergetic.metal.cdn.services.federation.secrets import decrypt_secret, encrypt_secret
from pymergetic.metal.cdn.settings import Settings


def test_normalize_mount_prefix() -> None:
    assert normalize_mount_prefix("a.b") == "a.b"
    assert normalize_mount_prefix("  hello  ") == "hello"
    with pytest.raises(ValueError):
        normalize_mount_prefix("")
    with pytest.raises(ValueError):
        normalize_mount_prefix("9bad")


def test_longest_prefix_mount() -> None:
    mounts = [("a", 1), ("a.b", 2), ("a.b.c", 3)]
    assert longest_prefix_mount("a.b.c.d", mounts) == ("a.b.c", 3)
    assert longest_prefix_mount("a.b.x", mounts) == ("a.b", 2)
    assert longest_prefix_mount("a", mounts) == ("a", 1)
    assert longest_prefix_mount("z", mounts) is None
    assert name_under_prefix("a.b", "a.b")
    assert not name_under_prefix("a.bx", "a.b")


def test_scopes_roundtrip() -> None:
    assert scopes_to_storage([SCOPE_FEDERATION_READ]) == '["federation:read"]'
    assert scopes_from_storage('["federation:read"]') == [SCOPE_FEDERATION_READ]
    assert key_allows([], SCOPE_FEDERATION_READ) is True
    assert key_allows([SCOPE_FEDERATION_READ], SCOPE_FEDERATION_READ) is True
    assert key_allows([SCOPE_FEDERATION_READ], "federation:publish") is False
    with pytest.raises(ValueError):
        normalize_scopes(["nope"])


def test_encrypt_secret_roundtrip() -> None:
    ct = encrypt_secret("mcdn_test_token", secret_key="dev-secret")
    assert decrypt_secret(ct, secret_key="dev-secret") == "mcdn_test_token"
    with pytest.raises(ValueError):
        decrypt_secret(ct, secret_key="other")


@pytest.fixture
async def admin_client(tmp_path: Path) -> AsyncIterator[AsyncClient]:
    settings = Settings(
        data_dir=tmp_path / "data",
        storage_root=tmp_path / "packs",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'fed.db'}",
        base_path="/cdn",
        require_auth=True,
        allow_open_registration=False,
        bootstrap_admin_email="admin@cdn.pymergetic.com",
        bootstrap_admin_password="x" * 16,
        csrf_enabled=False,
        rate_limit_enabled=False,
        debug=False,
        experimental=False,
        session_secret="test-fed-secret",
    )
    app = create_app(settings)
    transport = ASGITransport(app=app)
    async with (
        AsyncClient(transport=transport, base_url="http://test") as ac,
        app.router.lifespan_context(app),
    ):
        tok = await ac.post(
            "/cdn/auth/token",
            json={"email": "admin@cdn.pymergetic.com", "password": "x" * 16, "name": "t"},
        )
        assert tok.status_code == 200, tok.text
        key = tok.json()["key"]
        ac.headers["Authorization"] = f"Bearer {key}"
        yield ac


@pytest.mark.asyncio
async def test_federation_admin_flow(admin_client: AsyncClient) -> None:
    st = await admin_client.get("/cdn/admin/federation/status")
    assert st.status_code == 200
    assert st.json()["proxy_ready"] is True

    peer = await admin_client.post(
        "/cdn/admin/federation/peers",
        json={
            "label": "leaf",
            "base_url": "https://leaf.example/cdn",
            "public_browse_url": "https://leaf.example/cdn",
        },
    )
    assert peer.status_code == 201, peer.text
    peer_id = peer.json()["id"]

    mount = await admin_client.post(
        "/cdn/admin/federation/mounts",
        json={
            "prefix": "a.b",
            "peer_id": peer_id,
            "bearer_token": "mcdn_deadbeef_parentstoreschildtoken",
        },
    )
    assert mount.status_code == 201, mount.text
    body = mount.json()
    assert body["prefix"] == "a.b"
    assert body["has_credential"] is True
    assert body["peer_label"] == "leaf"
    mount_id = body["id"]

    pub = await admin_client.get("/cdn/federation/mounts")
    assert pub.status_code == 200
    assert pub.json()[0]["prefix"] == "a.b"
    assert "token" not in pub.text.lower() or "bearer" not in pub.text.lower()

    grant = await admin_client.post(
        "/cdn/admin/federation/grants/accept",
        json={
            "prefix": "a.b",
            "parent_label": "parent-cdn",
            "parent_base_url": "https://parent.example/cdn",
        },
    )
    assert grant.status_code == 201, grant.text
    assert grant.json()["api_key"].startswith("mcdn_")
    assert grant.json()["status"] == "active"

    bad = await admin_client.post(
        "/cdn/admin/federation/mounts",
        json={"prefix": "a.b", "peer_id": peer_id},
    )
    assert bad.status_code == 409

    rotated = await admin_client.put(
        f"/cdn/admin/federation/mounts/{mount_id}/credential",
        json={"bearer_token": "mcdn_cafe0000_rotatedtokenvaluehere"},
    )
    assert rotated.status_code == 200
    assert rotated.json()["has_credential"] is True


@pytest.mark.asyncio
async def test_federation_requires_admin(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path / "data",
        storage_root=tmp_path / "packs",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'fed2.db'}",
        base_path="/",
        require_auth=False,
        csrf_enabled=False,
        rate_limit_enabled=False,
        session_secret="x",
        experimental=False,
    )
    app = create_app(settings)
    transport = ASGITransport(app=app)
    async with (
        AsyncClient(transport=transport, base_url="http://test") as ac,
        app.router.lifespan_context(app),
    ):
        r = await ac.get("/admin/federation/status")
        assert r.status_code == 401
