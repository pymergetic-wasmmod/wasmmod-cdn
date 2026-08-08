"""Shared Inspect contract mounted via metal FastAPIAdapter."""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from pymergetic.metal.cdn.main import create_app
from pymergetic.metal.cdn.settings import Settings


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        storage_root=tmp_path / "packs",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 't.db'}",
        base_path="/cdn",
        session_secret="test-secret",
        public_origin=None,
        behind_proxy=False,
        rate_limit_enabled=False,
        csrf_enabled=False,
    )


@pytest.mark.asyncio
async def test_inspect_capabilities_and_stub(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Existing CDN health shape preserved.
        h = await ac.get("/cdn/health")
        assert h.status_code == 200
        assert h.json()["status"] == "ok"

        caps = await ac.get("/cdn/capabilities")
        assert caps.status_code == 200
        body = caps.json()
        assert body["role"] == "cdn"
        assert body["theme"] == "cdn"
        assert body["fastapi"] is True
        assert body["microdot"] is False

        stub = await ac.get("/cdn/inspect/self")
        assert stub.status_code == 501
        assert stub.json()["error"] == "NotImplemented"
