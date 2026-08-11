"""Base-path helpers."""

from __future__ import annotations

from pymergetic.wasmmod.cdn.paths import join_base, normalize_base_path, path_prefix


def test_normalize_base_path() -> None:
    assert normalize_base_path("/") == "/"
    assert normalize_base_path("/cdn") == "/cdn"
    assert normalize_base_path("/cdn/") == "/cdn"
    assert normalize_base_path("cdn") == "/cdn"


def test_join_base() -> None:
    assert path_prefix("/") == ""
    assert path_prefix("/cdn") == "/cdn"
    assert join_base("/", "health") == "/health"
    assert join_base("/cdn", "health") == "/cdn/health"
    assert join_base("/cdn", "channels", "lead") == "/cdn/channels/lead"
