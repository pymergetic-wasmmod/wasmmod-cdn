"""Browser REPL autoexec + shell session binding."""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from pymergetic.wasmmod.cdn.main import create_app
from pymergetic.wasmmod.cdn.settings import Settings
from pymergetic.wasmmod.cdn.web.repl_autoexec import render_autoexec


def test_render_autoexec_contains_boot_and_help() -> None:
    src = render_autoexec(
        cdn_base="http://127.0.0.1:8000/cdn",
        app_version="0.0-test",
        packages=["hello", "test_a"],
        session_id="11111111-1111-1111-1111-111111111111",
        principal="anon",
    )
    assert "import pymergetic.wasmmod as wasm" in src
    assert "wasm.cdn(CDN)" in src
    assert "wasm.install_hook()" in src
    assert "wasm.session_id(SESSION_ID)" in src
    assert '__import__("wasm")' not in src
    assert "def packages(" in src
    assert "refresh" in src
    assert "_PACKAGES_SRC" in src
    assert "WARN:" in src
    assert "baked lead" in src
    assert "def exports(" in src
    assert "wasm.catalog" in src
    assert "def help(" in src
    assert '"hello"' in src
    assert "sys.implementation.version" in src
    assert "wamr_version" in src
    assert "SESSION_ID" in src
    assert "11111111-1111-1111-1111-111111111111" in src
    assert "├──" in src and "└──" in src
    assert "ready" in src
    assert "packs" in src
    assert "ljust" not in src  # MicroPython str has no ljust
    assert "_pad(" in src
    assert "import hello as" not in src  # bootstrap must not load a pack
    compile(src, "<autoexec>", "exec")


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        storage_root=tmp_path / "packs",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 't.db'}",
        base_path="/cdn",
        experimental=True,
        experimental_repl=True,
        session_secret="test-secret",
        # Avoid Secure cookies from repo .env PUBLIC_ORIGIN=https://… (httpx + http://test).
        public_origin=None,
        behind_proxy=False,
        rate_limit_enabled=False,
        csrf_enabled=True,
    )


@pytest.mark.asyncio
async def test_autoexec_endpoint(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))
    transport = ASGITransport(app=app)
    async with (
        AsyncClient(transport=transport, base_url="http://test") as client,
        app.router.lifespan_context(app),
    ):
        r = await client.get("/cdn/repl/autoexec.py")
    assert r.status_code == 200
    assert "text/x-python" in r.headers.get("content-type", "")
    assert r.headers.get("X-Shell-Session-Id")
    body = r.text
    assert "wasm.cdn(CDN)" in body
    assert "wasmmod-cdn" in body
    assert "http://test/cdn" in body
    assert "SESSION_ID" in body
    compile(body, "<autoexec>", "exec")


@pytest.mark.asyncio
async def test_autoexec_cdn_query_override(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))
    transport = ASGITransport(app=app)
    async with (
        AsyncClient(transport=transport, base_url="http://test") as client,
        app.router.lifespan_context(app),
    ):
        r = await client.get(
            "/cdn/repl/autoexec.py",
            params={"cdn": "https://cdn.example/cdn"},
        )
    assert r.status_code == 200
    assert "https://cdn.example/cdn" in r.text
    assert "http://test/cdn" not in r.text


@pytest.mark.asyncio
async def test_autoexec_binds_anon_session_and_reuses(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))
    transport = ASGITransport(app=app)
    async with (
        AsyncClient(transport=transport, base_url="http://test") as client,
        app.router.lifespan_context(app),
    ):
        r1 = await client.get("/cdn/repl/autoexec.py")
        sid1 = r1.headers.get("X-Shell-Session-Id")
        assert sid1
        r2 = await client.get("/cdn/repl/autoexec.py")
        sid2 = r2.headers.get("X-Shell-Session-Id")
        assert sid2 == sid1
        listed = await client.get("/cdn/api/sessions")
        assert listed.status_code == 200
        data = listed.json()
        assert len(data) >= 1
        assert data[0]["id"] == sid1
        assert data[0]["cdn_base"].endswith("/cdn")
        assert data[0]["hook_on"] is True
        act = await client.get(f"/cdn/api/sessions/{sid1}/activity")
        assert act.status_code == 200
        body = act.json()
        assert body["window_minutes"] == 30
        assert len(body["buckets"]) >= 1
        assert any(e["kind"] == "autoexec" for e in body["recent"])


@pytest.mark.asyncio
async def test_pack_get_records_hit(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))
    transport = ASGITransport(app=app)
    async with (
        AsyncClient(transport=transport, base_url="http://test") as client,
        app.router.lifespan_context(app),
    ):
        boot = await client.get("/cdn/repl/autoexec.py")
        sid = boot.headers["X-Shell-Session-Id"]
        # Missing artifact is 404 — middleware only records status < 400.
        # Hit lead index instead (empty catalog is still 200).
        idx = await client.get("/cdn/index/lead")
        assert idx.status_code == 200
        act = await client.get(f"/cdn/api/sessions/{sid}/activity")
        kinds = {e["kind"] for e in act.json()["recent"]}
        assert "autoexec" in kinds
        assert "index" in kinds


@pytest.mark.asyncio
async def test_post_try_package_event(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))
    transport = ASGITransport(app=app)
    async with (
        AsyncClient(transport=transport, base_url="http://test") as client,
        app.router.lifespan_context(app),
    ):
        boot = await client.get("/cdn/repl/autoexec.py")
        sid = boot.headers["X-Shell-Session-Id"]
        csrf = await client.get("/cdn/auth/csrf")
        token = csrf.json()["csrf_token"]
        ev = await client.post(
            "/cdn/api/sessions/events",
            headers={"X-CSRF-Token": token},
            json={"kind": "try_package", "package": "hello", "session_id": sid},
        )
        assert ev.status_code == 200
        assert ev.json()["kind"] == "try_package"
        assert ev.json()["package"] == "hello"


@pytest.mark.asyncio
async def test_sessions_page(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))
    transport = ASGITransport(app=app)
    async with (
        AsyncClient(transport=transport, base_url="http://test") as client,
        app.router.lifespan_context(app),
    ):
        await client.get("/cdn/repl/autoexec.py")
        page = await client.get("/cdn/sessions")
    assert page.status_code == 200
    assert "Sessions" in page.text
    assert "loader entered" in page.text
    assert "<strong>anon</strong>" in page.text
    # web principal id (cookie anon_id) in brackets next to label
    assert 'title="web session principal id"' in page.text
    assert "[-" not in page.text  # not empty brackets
    assert "[" in page.text and "]" in page.text


@pytest.mark.asyncio
async def test_login_claims_anon_shell_sessions(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.csrf_enabled = False
    settings.allow_open_registration = True
    app = create_app(settings)
    transport = ASGITransport(app=app)
    async with (
        AsyncClient(transport=transport, base_url="http://test") as client,
        app.router.lifespan_context(app),
    ):
        boot = await client.get("/cdn/repl/autoexec.py")
        assert boot.status_code == 200
        sid = boot.headers["X-Shell-Session-Id"]
        await client.post(
            "/cdn/auth/register",
            json={"email": "shell@example.com", "password": "secret123", "display_name": "S"},
        )
        login = await client.post(
            "/cdn/auth/login",
            json={"email": "shell@example.com", "password": "secret123"},
        )
        assert login.status_code == 200
        listed = await client.get("/cdn/api/sessions")
        assert listed.status_code == 200
        ids = [row["id"] for row in listed.json()]
        assert sid in ids
        page = await client.get("/cdn/sessions")
        assert page.status_code == 200
        assert "shell@example.com" in page.text
        assert "Logout" in page.text
        await client.post("/cdn/auth/logout")
        page2 = await client.get("/cdn/channels/lead")
        assert page2.status_code == 200
        assert "Login" in page2.text
