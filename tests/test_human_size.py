"""human_size formatting."""

from __future__ import annotations

from pymergetic.wasmmod.cdn_client.format import human_size


def test_human_size_small() -> None:
    assert human_size(512) == "512 B"
    assert human_size(0) == "0 B"


def test_human_size_kib() -> None:
    assert human_size(9755) == "9.53 KiB (9755 B)"


def test_human_size_mib() -> None:
    assert human_size(1_500_000).endswith("(1500000 B)")
    assert "MiB" in human_size(1_500_000)


def test_human_size_none() -> None:
    assert human_size(None) == "?"
