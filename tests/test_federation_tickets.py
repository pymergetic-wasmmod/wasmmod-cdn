"""Ed25519 MetalFed tickets + proxy auth."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from pymergetic.metal.cdn.main import create_app
from pymergetic.metal.cdn.services.federation.scopes import (
    SCOPE_FEDERATION_PUBLISH,
    SCOPE_FEDERATION_READ,
)
from pymergetic.metal.cdn.services.federation.tickets import (
    generate_keypair,
    sign_ticket,
    verify_ticket,
)
from pymergetic.metal.cdn.settings import Settings


def test_ticket_roundtrip() -> None:
    pair = generate_keypair()
    token = sign_ticket(
        pair.private_b64,
        prefix="leaf",
        scopes=[SCOPE_FEDERATION_READ],
        hop=1,
        aud="http://child.test/cdn",
        key_id=pair.key_id,
    )
    assert token.startswith("MetalFed ")
    raw = token.split(" ", 1)[1]
    claims = verify_ticket(pair.public_b64, raw)
    assert claims.prefix == "leaf"
    assert claims.kid == pair.key_id
    assert SCOPE_FEDERATION_READ in claims.scopes


def test_ticket_rejects_wrong_key() -> None:
    a = generate_keypair()
    b = generate_keypair()
    token = sign_ticket(
        a.private_b64, prefix="x", scopes=[SCOPE_FEDERATION_READ], key_id=a.key_id
    )
    raw = token.split(" ", 1)[1]
    with pytest.raises(ValueError, match="signature|kid"):
        verify_ticket(b.public_b64, raw)


def _settings(tmp_path: Path, name: str, **kwargs: object) -> Settings:
    base: dict = {
        "data_dir": tmp_path / f"{name}-data",
        "storage_root": tmp_path / f"{name}-packs",
        "database_url": f"sqlite+aiosqlite:///{tmp_path / f'{name}.db'}",
        "base_path": "/cdn",
        "require_auth": True,
        "csrf_enabled": False,
        "rate_limit_enabled": False,
        "debug": False,
        "experimental": False,
        "session_secret": f"secret-{name}-ticket",
        "bootstrap_admin_email": f"{name}@cdn.pymergetic.com",
        "bootstrap_admin_password": "x" * 16,
        "allow_open_registration": False,
        "auto_claim_on_publish": True,
        "federation_allow_private_net": True,
    }
    base.update(kwargs)
    return Settings(**base)


@pytest.fixture
async def ticket_pair(tmp_path: Path) -> AsyncIterator[tuple[AsyncClient, AsyncClient]]:
    """Parent (fed key) + child (grant with parent_public_key); package on child."""
    child_app = create_app(_settings(tmp_path, "tchild"))
    parent_app = create_app(_settings(tmp_path, "tparent"))
    child_transport = ASGITransport(app=child_app)
    fed_client = httpx.AsyncClient(transport=child_transport, base_url="http://child.test")
    parent_app.state.federation_http_client = fed_client

    parent_transport = ASGITransport(app=parent_app)
    async with (
        child_app.router.lifespan_context(child_app),
        parent_app.router.lifespan_context(parent_app),
        AsyncClient(transport=child_transport, base_url="http://child.test") as child,
        AsyncClient(transport=parent_transport, base_url="http://parent.test") as parent,
    ):
        ctok = await child.post(
            "/cdn/auth/token",
            json={
                "email": "tchild@cdn.pymergetic.com",
                "password": "x" * 16,
                "name": "admin",
            },
        )
        assert ctok.status_code == 200, ctok.text
        child.headers["Authorization"] = f"Bearer {ctok.json()['key']}"

        ptok = await parent.post(
            "/cdn/auth/token",
            json={
                "email": "tparent@cdn.pymergetic.com",
                "password": "x" * 16,
                "name": "admin",
            },
        )
        assert ptok.status_code == 200, ptok.text
        parent.headers["Authorization"] = f"Bearer {ptok.json()['key']}"

        peer = await parent.post(
            "/cdn/admin/federation/peers",
            json={"label": "child", "base_url": "http://child.test/cdn"},
        )
        assert peer.status_code == 201, peer.text
        mount = await parent.post(
            "/cdn/admin/federation/mounts",
            json={"prefix": "leaf", "peer_id": peer.json()["id"]},
        )
        assert mount.status_code == 201, mount.text
        mount_id = mount.json()["id"]

        fed = await parent.post(f"/cdn/admin/federation/mounts/{mount_id}/fed-key")
        assert fed.status_code == 201, fed.text
        public_key = fed.json()["public_key"]

        grant = await child.post(
            "/cdn/admin/federation/grants/accept",
            json={
                "prefix": "leaf",
                "parent_label": "parent",
                "parent_public_key": public_key,
                "allow_publish": True,
            },
        )
        assert grant.status_code == 201, grant.text

        meta = {
            "package": "leaf.demo",
            "version": "0.1.0",
            "lead": True,
            "pin": True,
            "aot_version": 6,
            "deps": {},
        }
        files = [
            (
                "files",
                ("leaf.demo.wasm", b"\x00asm\x01\x00\x00\x00tick", "application/octet-stream"),
            ),
        ]
        pr = await child.post("/cdn/publish", data={"meta": json.dumps(meta)}, files=files)
        assert pr.status_code == 201, pr.text

        yield parent, child

    await fed_client.aclose()


@pytest.mark.asyncio
async def test_parent_reads_via_metalfed_ticket(
    ticket_pair: tuple[AsyncClient, AsyncClient],
) -> None:
    parent, _child = ticket_pair
    pkg = await parent.get("/cdn/packages/leaf.demo")
    assert pkg.status_code == 200, pkg.text
    assert pkg.json()["version"] == "0.1.0"
    art = await parent.get("/cdn/artifacts/lead/leaf.demo.wasm")
    assert art.status_code == 200
    assert art.content.endswith(b"tick")
    assert art.headers.get("x-metal-origin") == "remote"


@pytest.mark.asyncio
async def test_metalfed_auth_me_on_child(
    ticket_pair: tuple[AsyncClient, AsyncClient],
) -> None:
    _parent, child = ticket_pair
    pair = generate_keypair()
    grant = await child.post(
        "/cdn/admin/federation/grants/accept",
        json={
            "prefix": "leaf.alt",
            "parent_label": "p2",
            "parent_public_key": pair.public_b64,
        },
    )
    assert grant.status_code == 201, grant.text
    token = sign_ticket(
        pair.private_b64,
        prefix="leaf.alt",
        scopes=[SCOPE_FEDERATION_READ],
        key_id=pair.key_id,
    )
    me = await child.get("/cdn/auth/me", headers={"Authorization": token})
    assert me.status_code == 200, me.text
    assert "federation-bot" in me.json()["email"]


@pytest.mark.asyncio
async def test_upstream_publish_with_fed_key(
    ticket_pair: tuple[AsyncClient, AsyncClient],
) -> None:
    parent, child = ticket_pair
    mounts = await parent.get("/cdn/admin/federation/mounts")
    mount_id = mounts.json()[0]["id"]
    upd = await parent.patch(
        f"/cdn/admin/federation/mounts/{mount_id}",
        json={"direction": "push"},
    )
    assert upd.status_code == 200, upd.text

    meta = {
        "package": "leaf.up",
        "version": "0.2.0",
        "lead": True,
        "pin": False,
        "aot_version": 6,
        "deps": {},
    }
    files = [
        ("files", ("leaf.up.wasm", b"\x00asm\x01\x00\x00\x00fedp", "application/octet-stream")),
    ]
    pr = await parent.post(
        "/cdn/publish",
        data={"meta": json.dumps(meta), "upstream": "true"},
        files=files,
    )
    assert pr.status_code == 201, pr.text
    assert (await child.get("/cdn/packages/leaf.up")).status_code == 200
    assert SCOPE_FEDERATION_PUBLISH
