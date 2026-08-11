"""Bootstrap federation_mounts_json → DB (idempotent)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlmodel import select

from pymergetic.wasmmod.cdn.main import create_app
from pymergetic.wasmmod.cdn.services.federation.registry import FederationRegistry
from pymergetic.wasmmod.cdn.services.federation.tables import FederationMount, FederationPeer
from pymergetic.wasmmod.cdn.settings import Settings


def _settings(tmp_path: Path, **kwargs: object) -> Settings:
    base: dict = {
        "data_dir": tmp_path / "data",
        "storage_root": tmp_path / "packs",
        "database_url": f"sqlite+aiosqlite:///{tmp_path / 'boot-fed.db'}",
        "base_path": "/cdn",
        "csrf_enabled": False,
        "rate_limit_enabled": False,
        "debug": False,
        "experimental": False,
        "require_auth": False,
        "session_secret": "test-session-secret-bootstrap-fed",
        "federation_allow_private_net": True,
    }
    base.update(kwargs)
    return Settings(**base)


@pytest.mark.asyncio
async def test_apply_bootstrap_mounts_creates_then_skips(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    app = create_app(settings)
    payload = json.dumps(
        [
            {
                "prefix": "leaf.demo",
                "url": "http://127.0.0.1:9/cdn",
                "token": "mcdn_bootstrap_token_ok",
                "label": "leaf",
            }
        ]
    )
    async with app.router.lifespan_context(app), app.state.db.session_maker() as session:
        reg = FederationRegistry(
            session,
            secrets_key=settings.session_secret,
            max_hops=settings.federation_max_hops,
        )
        first = await reg.apply_bootstrap_mounts(
            payload, allow_private_net=True
        )
        assert any("created mount leaf.demo" in line for line in first)
        second = await reg.apply_bootstrap_mounts(
            payload, allow_private_net=True
        )
        assert any("skip mount leaf.demo" in line for line in second)
        mounts = await reg.list_mounts()
        assert len(mounts) == 1
        assert mounts[0].prefix == "leaf.demo"
        assert mounts[0].has_credential is True
        assert mounts[0].peer_label == "leaf"


@pytest.mark.asyncio
async def test_lifespan_applies_federation_mounts_json(tmp_path: Path) -> None:
    payload = json.dumps(
        [
            {
                "prefix": "boot.pkg",
                "url": "http://127.0.0.1:9/cdn",
                "token": "mcdn_lifespan_token_xx",
                "label": "boot-leaf",
            }
        ]
    )
    settings = _settings(tmp_path, federation_mounts_json=payload)
    app = create_app(settings)
    transport = ASGITransport(app=app)
    async with (
        AsyncClient(transport=transport, base_url="http://test") as client,
        app.router.lifespan_context(app),
    ):
        # Public mounts list (no secrets)
        res = await client.get("/cdn/federation/mounts")
        assert res.status_code == 200, res.text
        rows = res.json()
        assert any(r["prefix"] == "boot.pkg" for r in rows)

    async with app.state.db.session_maker() as session:
        peers = (await session.exec(select(FederationPeer))).all()
        mounts = (await session.exec(select(FederationMount))).all()
        assert len(peers) == 1
        assert peers[0].label == "boot-leaf"
        assert len(mounts) == 1
        assert mounts[0].notes.startswith("bootstrap:")


@pytest.mark.asyncio
async def test_bootstrap_rejects_private_url_without_flag(tmp_path: Path) -> None:
    settings = _settings(tmp_path, federation_allow_private_net=False)
    app = create_app(settings)
    payload = json.dumps(
        [{"prefix": "x", "url": "http://127.0.0.1:9/cdn", "token": "mcdn_abcdefgh"}]
    )
    async with app.router.lifespan_context(app), app.state.db.session_maker() as session:
        reg = FederationRegistry(
            session,
            secrets_key=settings.session_secret,
            max_hops=8,
        )
        lines = await reg.apply_bootstrap_mounts(payload, allow_private_net=False)
        assert any("not allowed" in line or "skip mount" in line for line in lines)
        assert await reg.list_mounts() == []
