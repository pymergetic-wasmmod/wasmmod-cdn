"""P1 federation read-proxy: parent forwards package/artifact misses to child."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from pymergetic.metal.cdn.main import create_app
from pymergetic.metal.cdn.services.federation.artifact_name import artifact_package_hint
from pymergetic.metal.cdn.services.federation.ssrf import validate_peer_url
from pymergetic.metal.cdn.settings import Settings


def test_artifact_package_hint() -> None:
    assert artifact_package_hint("leaf.demo.wasm") == "leaf.demo"
    assert artifact_package_hint("leaf.demo.wasm.zlib") == "leaf.demo"
    assert artifact_package_hint("bad..name.wasm") is None


def test_ssrf_blocks_loopback_ip() -> None:
    with pytest.raises(ValueError):
        validate_peer_url("http://127.0.0.1/cdn")
    assert validate_peer_url("https://leaf.example/cdn") == "https://leaf.example/cdn"
    assert (
        validate_peer_url("http://127.0.0.1/cdn", allow_private_net=True)
        == "http://127.0.0.1/cdn"
    )


def _settings(tmp_path: Path, name: str, **kwargs: object) -> Settings:
    base: dict = {
        "data_dir": tmp_path / f"{name}-data",
        "storage_root": tmp_path / f"{name}-packs",
        "database_url": f"sqlite+aiosqlite:///{tmp_path / f'{name}.db'}",
        "base_path": "/cdn",
        "require_auth": False,
        "csrf_enabled": False,
        "rate_limit_enabled": False,
        "debug": False,
        "experimental": False,
        "session_secret": f"secret-{name}",
        "bootstrap_admin_email": f"{name}@cdn.pymergetic.com",
        "bootstrap_admin_password": "x" * 16,
        "allow_open_registration": True,
    }
    base.update(kwargs)
    return Settings(**base)


@pytest.fixture
async def parent_child(tmp_path: Path) -> AsyncIterator[tuple[AsyncClient, AsyncClient]]:
    child_app = create_app(_settings(tmp_path, "child"))
    parent_app = create_app(_settings(tmp_path, "parent"))
    child_transport = ASGITransport(app=child_app)
    fed_client = httpx.AsyncClient(
        transport=child_transport,
        base_url="http://child.test",
    )
    parent_app.state.federation_http_client = fed_client

    parent_transport = ASGITransport(app=parent_app)
    async with (
        child_app.router.lifespan_context(child_app),
        parent_app.router.lifespan_context(parent_app),
        AsyncClient(transport=child_transport, base_url="http://child.test") as child,
        AsyncClient(transport=parent_transport, base_url="http://parent.test") as parent,
    ):
        # Publish only on child (open publish; no auth required for reads).
        meta = {
            "package": "leaf.demo",
            "version": "0.1.0",
            "lead": True,
            "pin": True,
            "aot_version": 6,
            "deps": {},
            "maintainer_email": "pub@cdn.pymergetic.com",
        }
        files = [
            (
                "files",
                ("leaf.demo.wasm", b"\x00asm\x01\x00\x00\x00leaf", "application/octet-stream"),
            ),
        ]
        pr = await child.post("/cdn/publish", data={"meta": json.dumps(meta)}, files=files)
        assert pr.status_code == 201, pr.text

        # Parent admin token + mount.
        tok = await parent.post(
            "/cdn/auth/token",
            json={
                "email": "parent@cdn.pymergetic.com",
                "password": "x" * 16,
                "name": "t",
            },
        )
        assert tok.status_code == 200, tok.text
        parent.headers["Authorization"] = f"Bearer {tok.json()['key']}"

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

        st = await parent.get("/cdn/admin/federation/status")
        assert st.json()["proxy_ready"] is True

        yield parent, child

    await fed_client.aclose()


@pytest.mark.asyncio
async def test_parent_forwards_package_and_artifact(
    parent_child: tuple[AsyncClient, AsyncClient],
) -> None:
    parent, child = parent_child
    # Sanity: only on child locally.
    assert (await child.get("/cdn/packages/leaf.demo")).status_code == 200
    assert (await parent.get("/cdn/packages/leaf.demo")).status_code == 200
    body = (await parent.get("/cdn/packages/leaf.demo")).json()
    assert body["version"] == "0.1.0"

    art = await parent.get("/cdn/artifacts/lead/leaf.demo.wasm")
    assert art.status_code == 200
    assert art.content.startswith(b"\x00asm")
    assert art.headers.get("x-metal-origin") == "remote"
    assert art.headers.get("x-metal-fed-mount") == "leaf"

    vers = await parent.get("/cdn/packages/leaf.demo/versions")
    assert vers.status_code == 200
    assert any(v["channel"] == "lead" for v in vers.json())

    # Local shadow: publish same name on parent → local wins (no remote header).
    meta = {
        "package": "leaf.demo",
        "version": "9.9.9",
        "lead": True,
        "pin": False,
        "aot_version": 6,
        "deps": {},
        "maintainer_email": "parent@cdn.pymergetic.com",
    }
    files = [
        ("files", ("leaf.demo.wasm", b"\x00asm\x01\x00\x00\x00locl", "application/octet-stream")),
    ]
    pr = await parent.post("/cdn/publish", data={"meta": json.dumps(meta)}, files=files)
    assert pr.status_code == 201, pr.text
    local = await parent.get("/cdn/packages/leaf.demo")
    assert local.json()["version"] == "9.9.9"
    art2 = await parent.get("/cdn/artifacts/lead/leaf.demo.wasm")
    assert art2.content.endswith(b"locl")
    assert art2.headers.get("x-metal-origin") is None


@pytest.mark.asyncio
async def test_catalog_lists_remote_packages(
    parent_child: tuple[AsyncClient, AsyncClient],
) -> None:
    parent, _child = parent_child
    # Drop local shadow package if prior test polluted — fresh fixture each time.
    rows = (await parent.get("/cdn/packages?channel=lead")).json()
    names = {r["name"] for r in rows}
    assert "leaf.demo" in names
    remote = next(r for r in rows if r["name"] == "leaf.demo")
    assert remote["origin"] == "remote"
    assert remote["mount_prefix"] == "leaf"
    assert remote["peer_browse_url"]

    home = await parent.get("/cdn/channels/lead")
    assert home.status_code == 200
    assert b"is-remote" in home.content
    assert b"Visit remote" in home.content
    assert b'pill-remote' in home.content


@pytest.mark.asyncio
async def test_inspect_forwards_remote_artifact(
    parent_child: tuple[AsyncClient, AsyncClient],
) -> None:
    parent, _child = parent_child
    insp = await parent.get("/cdn/artifacts/lead/leaf.demo.wasm/inspect")
    assert insp.status_code == 200
    assert "files" in insp.json() or "kind" in insp.json() or isinstance(insp.json(), dict)
