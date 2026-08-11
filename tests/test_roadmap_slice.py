"""ACL / lifecycle / index / ops coverage for the remaining roadmap slice."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from pymergetic.wasmmod.cdn.main import create_app
from pymergetic.wasmmod.cdn.settings import Settings
from pymergetic.wasmmod.cdn.storage import LocalObjectStorage, collect_orphan_keys


@pytest.fixture
async def client(tmp_path: Path):
    settings = Settings(
        data_dir=tmp_path / "data",
        storage_root=tmp_path / "packs",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'slice.db'}",
        base_path="/cdn",
        require_auth=True,
        session_secret="test-secret",
        allow_open_registration=True,
        csrf_enabled=False,
        rate_limit_enabled=False,
        index_signing_key="test-hmac",
        metrics_enabled=True,
        debug=False,
    )
    app = create_app(settings)
    transport = ASGITransport(app=app)
    async with (
        AsyncClient(transport=transport, base_url="http://test") as ac,
        app.router.lifespan_context(app),
    ):
        yield ac


async def _token(ac: AsyncClient, email: str) -> str:
    await ac.post(
        "/cdn/auth/register",
        json={"email": email, "password": "secret123", "display_name": email},
    )
    tok = await ac.post(
        "/cdn/auth/token",
        json={"email": email, "password": "secret123", "name": "t"},
    )
    assert tok.status_code == 200, tok.text
    return tok.json()["key"]


async def _publish(
    ac: AsyncClient, token: str, package: str, version: str, **extra: object
) -> dict:
    meta = {
        "package": package,
        "version": version,
        "lead": True,
        "pin": True,
        "deps": {},
        **extra,
    }
    files = [
        (
            "files",
            (
                f"{package.split('/')[-1]}.wasm",
                b"\x00asm\x01\x00\x00\x00x",
                "application/octet-stream",
            ),
        )
    ]
    r = await ac.post(
        "/cdn/publish",
        data={"meta": json.dumps(meta)},
        files=files,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201, r.text
    return r.json()


@pytest.mark.asyncio
async def test_scoped_claim_private_successor_index(client: AsyncClient) -> None:
    token = await _token(client, "owner@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    claim = await client.post("/cdn/packages/acme/hello/claim", headers=headers)
    assert claim.status_code == 200, claim.text
    assert claim.json()["package_name"] == "acme/hello"

    await _publish(client, token, "acme/hello", "1.0.0", description="scoped", license="MIT")

    vis = await client.put(
        "/cdn/packages/acme/hello/visibility",
        json={"visibility": "private"},
        headers=headers,
    )
    assert vis.status_code == 200, vis.text

    anon = await client.get("/cdn/packages/acme/hello")
    assert anon.status_code == 404

    ok = await client.get("/cdn/packages/acme/hello", headers=headers)
    assert ok.status_code == 200
    assert ok.json()["license"] == "MIT"

    succ = await client.post(
        "/cdn/packages/acme/hello/successor",
        json={"successor": "acme/hello2", "channel": "lead"},
        headers=headers,
    )
    assert succ.status_code == 200, succ.text
    assert succ.json()["successor"] == "acme/hello2"

    idx = await client.get("/cdn/index/lead")
    assert idx.status_code == 200
    body = idx.json()
    assert "signature" in body
    assert body["signature"]

    metrics = await client.get("/cdn/metrics")
    assert metrics.status_code == 200
    assert "wasmmod_cdn_requests_total" in metrics.text


@pytest.mark.asyncio
async def test_transfer_maintainer_cannot_yank(client: AsyncClient) -> None:
    owner = await _token(client, "own@example.com")
    other = await _token(client, "other@example.com")
    oh = {"Authorization": f"Bearer {owner}"}
    mh = {"Authorization": f"Bearer {other}"}

    me = await client.get("/cdn/auth/me", headers=mh)
    other_id = me.json()["id"]

    await _publish(client, owner, "xfer", "1.0.0")
    grant = await client.post(
        "/cdn/acl",
        json={"package_name": "xfer", "user_id": other_id, "role": "maintainer"},
        headers=oh,
    )
    assert grant.status_code == 201, grant.text

    denied = await client.post(
        "/cdn/packages/xfer/yank",
        json={"reason": "nope", "channel": "lead"},
        headers=mh,
    )
    assert denied.status_code == 403

    transferred = await client.post(
        "/cdn/packages/xfer/transfer",
        json={"to_user_id": other_id},
        headers=oh,
    )
    assert transferred.status_code == 200, transferred.text

    yanked = await client.post(
        "/cdn/packages/xfer/yank",
        json={"reason": "ok", "channel": "lead"},
        headers=mh,
    )
    assert yanked.status_code == 200
    assert yanked.json()["yanked"] is True


@pytest.mark.asyncio
async def test_closure_and_gc(client: AsyncClient, tmp_path: Path) -> None:
    token = await _token(client, "dep@example.com")
    await _publish(client, token, "lib", "0.1.0")
    await _publish(client, token, "app", "1.0.0", deps={"lib": "0.1.0"})

    closure = await client.get("/cdn/packages/app/closure")
    assert closure.status_code == 200, closure.text
    names = [x["name"] for x in closure.json()["order"]]
    assert names == ["lib", "app"]

    # Orphan blob GC (local storage via app state)
    storage = LocalObjectStorage(tmp_path / "packs")
    # Use the app's storage by writing an orphan through the ready client path
    # — instead exercise collect_orphan_keys on a fresh local store with index.
    await storage.put_bytes(
        "index.json",
        b'{"schema":1,"channel":"lead","generated":"2026-08-04T00:00:00Z","packages":{}}',
    )
    await storage.put_bytes("orphan.bin", b"x")
    orphans = await collect_orphan_keys(storage)
    assert "orphan.bin" in orphans


@pytest.mark.asyncio
async def test_presign_local(client: AsyncClient) -> None:
    token = await _token(client, "pre@example.com")
    r = await client.post(
        "/cdn/publish/presign",
        json={
            "package": "pre",
            "version": "1.0.0",
            "filenames": ["pre.wasm"],
            "lead": True,
            "pin": True,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    uploads = r.json()["uploads"]
    assert len(uploads) == 2
    assert uploads[0]["url"].startswith("local://put/")


@pytest.mark.asyncio
async def test_org_team_acl(client: AsyncClient) -> None:
    owner = await _token(client, "orgowner@example.com")
    member = await _token(client, "teammate@example.com")
    oh = {"Authorization": f"Bearer {owner}"}
    mh = {"Authorization": f"Bearer {member}"}
    member_id = (await client.get("/cdn/auth/me", headers=mh)).json()["id"]

    org = await client.post("/cdn/orgs", json={"slug": "acme", "display_name": "Acme"}, headers=oh)
    assert org.status_code == 201, org.text
    org_id = org.json()["id"]
    team = await client.post(
        f"/cdn/orgs/{org_id}/teams",
        json={"slug": "publishers"},
        headers=oh,
    )
    assert team.status_code == 201, team.text
    team_id = team.json()["id"]
    add = await client.post(
        f"/cdn/orgs/teams/{team_id}/members",
        json={"user_id": member_id, "role": "maintainer"},
        headers=oh,
    )
    assert add.status_code == 204, add.text

    await _publish(client, owner, "teampkg", "1.0.0")
    grant = await client.post(
        "/cdn/orgs/team-acl",
        json={"package_name": "teampkg", "team_id": team_id, "role": "maintainer"},
        headers=oh,
    )
    assert grant.status_code == 201, grant.text

    # Maintainer via team can promote
    pin_only = {
        "package": "teampkg",
        "version": "1.1.0",
        "lead": False,
        "pin": True,
        "deps": {},
    }
    files = [("files", ("teampkg.wasm", b"\x00asm\x01\x00\x00\x00y", "application/octet-stream"))]
    pub = await client.post(
        "/cdn/publish",
        data={"meta": json.dumps(pin_only)},
        files=files,
        headers=mh,
    )
    assert pub.status_code == 201, pub.text
