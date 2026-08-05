"""Shared pytest fixtures — isolate Settings from developer .env."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_metal_cdn_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """HTTP test clients use ``http://test``; a Secure session cookie from
    ``METAL_CDN_PUBLIC_ORIGIN=https://…`` in a local ``.env`` would never be sent.
    """
    monkeypatch.delenv("METAL_CDN_PUBLIC_ORIGIN", raising=False)
    monkeypatch.setenv("METAL_CDN_PUBLIC_ORIGIN", "")
