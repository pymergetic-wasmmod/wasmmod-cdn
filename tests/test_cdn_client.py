"""Shared pymergetic.metal.cdn_client surface against a live ASGI app."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any
from urllib.error import HTTPError as UrllibHTTPError
from urllib.parse import urlparse

import pytest
from starlette.testclient import TestClient

import pymergetic.metal.cdn_client.client as client_mod
from pymergetic.metal.cdn.main import create_app
from pymergetic.metal.cdn.settings import Settings
from pymergetic.metal.cdn_client import (
    TOKEN_SOURCE_API_KEY,
    CdnClient,
    ClientError,
    clear_token,
    load_config,
    save_config,
    token_source,
)


@pytest.fixture
def app_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Spin ASGI app; route CdnClient urllib calls through Starlette TestClient."""
    settings = Settings(
        data_dir=tmp_path / "data",
        storage_root=tmp_path / "packs",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'client.db'}",
        base_path="/cdn",
        require_auth=True,
        session_secret="test-secret",
        allow_open_registration=True,
        csrf_enabled=False,
        rate_limit_enabled=False,
        debug=False,
    )
    app = create_app(settings)
    with TestClient(app) as ac:

        class _Resp:
            def __init__(self, status: int, headers: dict[str, str], body: bytes) -> None:
                self.status = status
                self.headers = headers
                self._body = body

            def read(self) -> bytes:
                return self._body

            def __enter__(self) -> Any:
                return self

            def __exit__(self, *args: object) -> None:
                return None

        def fake_urlopen(req: Any, timeout: float = 60.0) -> _Resp:
            del timeout
            method = req.get_method()
            headers = {k: v for k, v in req.headers.items()}
            data = req.data
            parsed = urlparse(req.get_full_url())
            path = parsed.path + (f"?{parsed.query}" if parsed.query else "")
            resp = ac.request(method, path, content=data, headers=headers)
            hdrs = {k: v for k, v in resp.headers.items()}
            if resp.status_code == 304 or resp.status_code >= 400:
                raise UrllibHTTPError(
                    req.get_full_url(),
                    resp.status_code,
                    str(resp.status_code),
                    hdrs,  # type: ignore[arg-type]
                    io.BytesIO(resp.content),
                )
            return _Resp(resp.status_code, hdrs, resp.content)

        monkeypatch.setattr(client_mod, "urlopen", fake_urlopen)
        yield "http://test/cdn"


def test_config_token_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    assert load_config() == {}
    path = save_config({"url": "http://x/cdn", "token": "mcdn_test", "email": "a@b.c"})
    assert path.is_file()
    cfg = load_config()
    assert cfg["token_source"] == TOKEN_SOURCE_API_KEY
    assert token_source(cfg) == TOKEN_SOURCE_API_KEY
    clear_token()
    assert "token" not in load_config()


def test_client_publish_claim_get_download(app_url: str, tmp_path: Path) -> None:
    client = CdnClient(app_url)
    client.register("pub@example.com", "secret123", display_name="Pub")
    created = client.create_api_key_with_password("pub@example.com", "secret123", name="t")
    assert created["key"].startswith("mcdn_")
    authed = CdnClient(app_url, token=created["key"])
    assert authed.me()["email"] == "pub@example.com"

    claimed = authed.claim("clipkg")
    assert claimed["package_name"] == "clipkg"

    wasm = tmp_path / "clipkg.wasm"
    wasm.write_bytes(b"\x00asm\x01\x00\x00\x00client")
    result = authed.publish(package="clipkg", version="1.0.0", files=[wasm])
    assert "channels" in result

    entry = authed.get_package("clipkg")
    assert entry["version"] == "1.0.0"
    assert any(
        a["path"] == "clipkg.wasm" or a["path"].endswith("clipkg.wasm") for a in entry["artifacts"]
    )

    found = authed.search("clip")
    assert any(p.get("name") == "clipkg" for p in found)

    dl = authed.download_artifact("clipkg.wasm")
    assert dl.data == wasm.read_bytes()
    assert dl.etag
    assert not dl.not_modified

    again = authed.download_artifact("clipkg.wasm", if_none_match=dl.etag)
    assert again.not_modified
    assert again.status == 304

    promoted = authed.promote("clipkg", "1.0.0")
    assert "channels" in promoted

    yanked = authed.yank("clipkg", reason="test yank", channel="lead")
    assert yanked.get("yanked") is True


def test_from_config_requires_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    with pytest.raises(ClientError, match="CDN URL not set"):
        CdnClient.from_config()

    save_config({"url": "http://x/cdn"})
    client = CdnClient.from_config(require_token=False)
    assert client.base_url.endswith("/cdn/")
    assert client.token is None

    with pytest.raises(ClientError, match="not logged in"):
        CdnClient.from_config(require_token=True)
