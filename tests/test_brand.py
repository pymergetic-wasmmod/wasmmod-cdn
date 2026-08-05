"""Branding URL resolve, CORS, and header mark wiring."""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from pymergetic.metal.cdn.main import create_app
from pymergetic.metal.cdn.settings import Settings
from pymergetic.metal.cdn.web.routes import configure_web, resolve_brand_logo_url


def _settings(tmp_path: Path, **kwargs: object) -> Settings:
    base: dict = dict(
        data_dir=tmp_path / "data",
        storage_root=tmp_path / "packs",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'brand.db'}",
        base_path="/cdn",
        csrf_enabled=False,
        rate_limit_enabled=False,
        debug=False,
        experimental=False,
    )
    base.update(kwargs)
    return Settings(**base)


def test_display_brand_name_falls_back_to_app_name(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    assert s.display_brand_name == "metal-cdn"
    s2 = _settings(tmp_path, brand_name="  acme-cdn  ")
    assert s2.display_brand_name == "acme-cdn"


def test_brand_logo_url_rejects_dangerous_schemes(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        _settings(tmp_path, brand_logo_url="javascript:alert(1)")
    with pytest.raises(ValidationError):
        _settings(tmp_path, brand_logo_url="ftp://evil.example/x.png")


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, "/cdn/static/img/pymergetic.png"),
        ("https://assets.example/logo.png", "https://assets.example/logo.png"),
        ("//cdn.example/mark.svg", "//cdn.example/mark.svg"),
        ("/cdn/static/img/custom.png", "/cdn/static/img/custom.png"),
        ("img/custom.png", "/cdn/static/img/custom.png"),
    ],
)
def test_resolve_brand_logo_url(raw: str | None, expected: str) -> None:
    configure_web("/cdn")
    default = "/cdn/static/img/pymergetic.png"
    settings = type("S", (), {"brand_logo_url": raw})()
    assert resolve_brand_logo_url(settings, default_href=default) == expected


def test_resolve_brand_logo_url_blank_uses_default() -> None:
    configure_web("/cdn")
    default = "/cdn/static/img/pymergetic.png"
    settings = type("S", (), {"brand_logo_url": ""})()
    assert resolve_brand_logo_url(settings, default_href=default) == default


@pytest.mark.asyncio
async def test_custom_brand_in_shell(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        brand_name="acme-cdn",
        brand_logo_url="https://assets.example/acme.png",
        cors_origins=["https://ui.example"],
    )
    app = create_app(settings)
    transport = ASGITransport(app=app)
    async with (
        AsyncClient(transport=transport, base_url="http://test") as ac,
        app.router.lifespan_context(app),
    ):
        home = await ac.get("/cdn/channels/lead")
        assert home.status_code == 200
        assert b"acme-cdn" in home.content
        assert b"https://assets.example/acme.png" in home.content
        assert b'class="brand-mark"' in home.content

        opts = await ac.options(
            "/cdn/health",
            headers={
                "Origin": "https://ui.example",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert opts.headers.get("access-control-allow-origin") == "https://ui.example"

        img = await ac.get("/cdn/static/img/pymergetic.png")
        assert img.status_code == 200
        assert img.headers.get("access-control-allow-origin") == "*"
        assert img.headers.get("cross-origin-resource-policy") == "cross-origin"


@pytest.mark.asyncio
async def test_cors_star_default(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    app = create_app(settings)
    transport = ASGITransport(app=app)
    async with (
        AsyncClient(transport=transport, base_url="http://test") as ac,
        app.router.lifespan_context(app),
    ):
        r = await ac.get("/cdn/health", headers={"Origin": "https://anywhere.example"})
        assert r.status_code == 200
        assert r.headers.get("access-control-allow-origin") == "*"
