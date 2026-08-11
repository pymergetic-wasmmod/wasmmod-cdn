"""Publish upstream (PUSH mount) foothold."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from pymergetic.wasmmod.cdn.main import create_app
from pymergetic.wasmmod.cdn.settings import Settings


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
        "session_secret": f"secret-{name}-upstream",
        "bootstrap_admin_email": f"{name}@cdn.pymergetic.com",
        "bootstrap_admin_password": "x" * 16,
        "allow_open_registration": False,
        "auto_claim_on_publish": True,
        "federation_allow_private_net": True,
    }
    base.update(kwargs)
    return Settings(**base)


@pytest.fixture
async def push_pair(tmp_path: Path) -> AsyncIterator[tuple[AsyncClient, AsyncClient]]:
    child_app = create_app(_settings(tmp_path, "uchild"))
    parent_app = create_app(_settings(tmp_path, "uparent"))
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
                "email": "uchild@cdn.pymergetic.com",
                "password": "x" * 16,
                "name": "admin",
            },
        )
        assert ctok.status_code == 200, ctok.text
        child.headers["Authorization"] = f"Bearer {ctok.json()['key']}"

        grant = await child.post(
            "/cdn/admin/federation/grants/accept",
            json={
                "prefix": "leaf",
                "parent_label": "parent",
                "allow_publish": True,
            },
        )
        assert grant.status_code == 201, grant.text
        fed_key = grant.json()["api_key"]

        ptok = await parent.post(
            "/cdn/auth/token",
            json={
                "email": "uparent@cdn.pymergetic.com",
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
            json={
                "prefix": "leaf",
                "peer_id": peer.json()["id"],
                "direction": "push",
                "bearer_token": fed_key,
            },
        )
        assert mount.status_code == 201, mount.text
        assert mount.json()["direction"] == "push"

        yield parent, child

    await fed_client.aclose()


@pytest.mark.asyncio
async def test_publish_upstream_to_push_mount(
    push_pair: tuple[AsyncClient, AsyncClient],
) -> None:
    parent, child = push_pair
    meta = {
        "package": "leaf.up",
        "version": "0.2.0",
        "lead": True,
        "pin": True,
        "aot_version": 6,
        "deps": {},
        "maintainer_email": "uparent@cdn.pymergetic.com",
    }
    files = [
        ("files", ("leaf.up.wasm", b"\x00asm\x01\x00\x00\x00upst", "application/octet-stream")),
    ]
    pr = await parent.post(
        "/cdn/publish",
        data={"meta": json.dumps(meta), "upstream": "true"},
        files=files,
    )
    assert pr.status_code == 201, pr.text
    assert pr.json()["package"] == "leaf.up"
    assert pr.headers.get("x-metal-origin") == "remote"
    assert pr.headers.get("x-metal-fed-mount") == "leaf"

    assert (await child.get("/cdn/packages/leaf.up")).status_code == 200
    assert (await parent.get("/cdn/packages/leaf.up")).status_code == 404

    art = await child.get("/cdn/artifacts/lead/leaf.up.wasm")
    assert art.status_code == 200
    assert art.content.endswith(b"upst")


@pytest.mark.asyncio
async def test_publish_upstream_requires_push_mount(
    push_pair: tuple[AsyncClient, AsyncClient],
) -> None:
    parent, _child = push_pair
    meta = {
        "package": "other.pkg",
        "version": "0.1.0",
        "lead": True,
        "pin": False,
        "aot_version": 6,
        "deps": {},
    }
    files = [
        ("files", ("other.pkg.wasm", b"\x00asm\x01\x00\x00\x00xxxx", "application/octet-stream")),
    ]
    pr = await parent.post(
        "/cdn/publish",
        data={"meta": json.dumps(meta), "upstream": "true"},
        files=files,
    )
    assert pr.status_code == 404
    assert "PUSH" in pr.json()["detail"]


@pytest.mark.asyncio
async def test_read_only_grant_cannot_publish_on_child(
    push_pair: tuple[AsyncClient, AsyncClient],
) -> None:
    _parent, child = push_pair
    grant = await child.post(
        "/cdn/admin/federation/grants/accept",
        json={"prefix": "ro", "parent_label": "p2", "allow_publish": False},
    )
    assert grant.status_code == 201, grant.text
    key = grant.json()["api_key"]
    me = await child.get("/cdn/auth/me", headers={"Authorization": f"Bearer {key}"})
    assert me.status_code == 200
    denied = await child.post(
        "/cdn/publish",
        headers={"Authorization": f"Bearer {key}"},
        data={
            "meta": json.dumps(
                {
                    "package": "ro.x",
                    "version": "0.1.0",
                    "lead": True,
                    "pin": False,
                    "aot_version": 6,
                    "deps": {},
                }
            )
        },
        files=[("files", ("ro.x.wasm", b"\x00asm", "application/octet-stream"))],
    )
    assert denied.status_code == 403


@pytest.mark.asyncio
async def test_upstream_publish_claimed_package_via_grant_prefix(
    push_pair: tuple[AsyncClient, AsyncClient],
) -> None:
    """Federation bot may publish under grant prefix even when package is claimed."""
    parent, child = push_pair
    # Child admin claims leaf.owned before federated publish.
    claim = await child.post("/cdn/packages/leaf.owned/claim")
    assert claim.status_code == 200, claim.text

    meta = {
        "package": "leaf.owned",
        "version": "0.3.0",
        "lead": True,
        "pin": False,
        "aot_version": 6,
        "deps": {},
        "maintainer_email": "uparent@cdn.pymergetic.com",
    }
    files = [
        (
            "files",
            ("leaf.owned.wasm", b"\x00asm\x01\x00\x00\x00own", "application/octet-stream"),
        ),
    ]
    pr = await parent.post(
        "/cdn/publish",
        data={"meta": json.dumps(meta), "upstream": "true"},
        files=files,
    )
    assert pr.status_code == 201, pr.text
    assert (await child.get("/cdn/packages/leaf.owned")).status_code == 200


@pytest.mark.asyncio
async def test_cli_parser_upstream_flag() -> None:
    from pymergetic.wasmmod.cdn.cli_parser import build_parser

    ns = build_parser().parse_args(
        ["publish", "leaf.x", "0.1.0", "x.wasm", "--upstream", "--also-local", "--no-pin"]
    )
    assert ns.upstream is True
    assert ns.also_local is True
    assert ns.no_pin is True


@pytest.mark.asyncio
async def test_dual_write_upstream_and_local(
    push_pair: tuple[AsyncClient, AsyncClient],
) -> None:
    parent, child = push_pair
    meta = {
        "package": "leaf.dual",
        "version": "0.4.0",
        "lead": True,
        "pin": False,
        "aot_version": 6,
        "deps": {},
        "maintainer_email": "uparent@cdn.pymergetic.com",
    }
    files = [
        (
            "files",
            ("leaf.dual.wasm", b"\x00asm\x01\x00\x00\x00dual", "application/octet-stream"),
        ),
    ]
    pr = await parent.post(
        "/cdn/publish",
        data={"meta": json.dumps(meta), "upstream": "true", "also_local": "true"},
        files=files,
    )
    assert pr.status_code == 201, pr.text
    assert pr.headers.get("x-metal-fed-dual-write") == "1"
    assert (await child.get("/cdn/packages/leaf.dual")).status_code == 200
    assert (await parent.get("/cdn/packages/leaf.dual")).status_code == 200
