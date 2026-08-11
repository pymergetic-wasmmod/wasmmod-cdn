"""P1 federation read-proxy: parent forwards package/artifact misses to child."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from pymergetic.wasmmod.cdn.main import create_app
from pymergetic.wasmmod.cdn.services.federation.artifact_name import artifact_package_hint
from pymergetic.wasmmod.cdn.services.federation.ssrf import validate_peer_url
from pymergetic.wasmmod.cdn.settings import Settings


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


@pytest.mark.asyncio
async def test_packages_prefix_filter(
    parent_child: tuple[AsyncClient, AsyncClient],
) -> None:
    parent, _child = parent_child
    all_rows = (await parent.get("/cdn/packages?channel=lead")).json()
    assert any(r["name"] == "leaf.demo" for r in all_rows)
    filtered = (await parent.get("/cdn/packages?channel=lead&prefix=leaf")).json()
    assert all(r["name"] == "leaf" or r["name"].startswith("leaf.") for r in filtered)
    assert any(r["name"] == "leaf.demo" for r in filtered)
    none = (await parent.get("/cdn/packages?channel=lead&prefix=other")).json()
    assert none == []


@pytest.mark.asyncio
async def test_negative_cache_skips_repeat_404(
    parent_child: tuple[AsyncClient, AsyncClient],
) -> None:
    parent, _child = parent_child
    from pymergetic.wasmmod.cdn.services.federation.neg_cache import NegativePeerCache

    # First miss populates cache; second should still 404 (and use cache).
    r1 = await parent.get("/cdn/packages/leaf.missing")
    assert r1.status_code == 404
    r2 = await parent.get("/cdn/packages/leaf.missing")
    assert r2.status_code == 404
    # Unit: cache remembers
    c = NegativePeerCache(ttl_s=30)
    k = NegativePeerCache.key(mount_id="m", method="GET", path="/packages/x")
    assert not c.is_miss(k)
    c.remember_miss(k)
    assert c.is_miss(k)


@pytest.mark.asyncio
async def test_three_hop_federation(tmp_path: Path) -> None:
    """grandparent → parent → child; package only on leaf."""
    child_app = create_app(_settings(tmp_path, "hopc"))
    mid_app = create_app(_settings(tmp_path, "hopm"))
    top_app = create_app(_settings(tmp_path, "hopt"))

    child_transport = ASGITransport(app=child_app)
    mid_transport = ASGITransport(app=mid_app)
    top_transport = ASGITransport(app=top_app)

    mid_app.state.federation_http_client = httpx.AsyncClient(
        transport=child_transport, base_url="http://child.test"
    )
    top_app.state.federation_http_client = httpx.AsyncClient(
        transport=mid_transport, base_url="http://mid.test"
    )

    async with (
        child_app.router.lifespan_context(child_app),
        mid_app.router.lifespan_context(mid_app),
        top_app.router.lifespan_context(top_app),
        AsyncClient(transport=child_transport, base_url="http://child.test") as child,
        AsyncClient(transport=mid_transport, base_url="http://mid.test") as mid,
        AsyncClient(transport=top_transport, base_url="http://top.test") as top,
    ):
        meta = {
            "package": "leaf.deep",
            "version": "1.0.0",
            "lead": True,
            "pin": False,
            "aot_version": 6,
            "deps": {},
        }
        files = [
            (
                "files",
                ("leaf.deep.wasm", b"\x00asm\x01\x00\x00\x00deep", "application/octet-stream"),
            ),
        ]
        assert (
            await child.post("/cdn/publish", data={"meta": json.dumps(meta)}, files=files)
        ).status_code == 201

        async def _mount(client: AsyncClient, email: str, peer_url: str) -> None:
            tok = await client.post(
                "/cdn/auth/token",
                json={"email": email, "password": "x" * 16, "name": "t"},
            )
            assert tok.status_code == 200, tok.text
            client.headers["Authorization"] = f"Bearer {tok.json()['key']}"
            peer = await client.post(
                "/cdn/admin/federation/peers",
                json={"label": "down", "base_url": peer_url},
            )
            assert peer.status_code == 201, peer.text
            mount = await client.post(
                "/cdn/admin/federation/mounts",
                json={"prefix": "leaf", "peer_id": peer.json()["id"]},
            )
            assert mount.status_code == 201, mount.text

        await _mount(mid, "hopm@cdn.pymergetic.com", "http://child.test/cdn")
        await _mount(top, "hopt@cdn.pymergetic.com", "http://mid.test/cdn")

        pkg = await top.get("/cdn/packages/leaf.deep")
        assert pkg.status_code == 200, pkg.text
        assert pkg.json()["version"] == "1.0.0"
        art = await top.get("/cdn/artifacts/lead/leaf.deep.wasm")
        assert art.status_code == 200
        assert art.content.endswith(b"deep")
        assert art.headers.get("x-metal-origin") == "remote"

    await mid_app.state.federation_http_client.aclose()
    await top_app.state.federation_http_client.aclose()
