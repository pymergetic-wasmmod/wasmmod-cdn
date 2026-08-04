"""CLI error formatting (shared by metal-cdn + wasmmod)."""

from __future__ import annotations

from pymergetic.metal.cdn_client import ClientError, format_client_error, format_error
from pymergetic.metal.cdn_client.errors import hints_for_client_error


def test_format_error_with_hints() -> None:
    msg = format_error("metal-cdn", "boom", "try this", "and that")
    assert msg == "metal-cdn: boom\n  try this\n  and that"


def test_409_force_hint() -> None:
    exc = ClientError("pin @0.1.0 already has package hello (immutable)", status=409)
    hints = hints_for_client_error(exc, force=False)
    assert any("--force" in h for h in hints)
    assert hints_for_client_error(exc, force=True) == []


def test_format_client_error_auth() -> None:
    exc = ClientError("unauthorized", status=401)
    text = format_client_error(exc, prog="wasmmod publish")
    assert text.startswith("wasmmod publish: unauthorized")
    assert "metal-cdn login" in text
    assert "whoami" in text


def test_connection_hint() -> None:
    exc = ClientError("connection failed: Connection refused")
    text = format_client_error(exc, prog="metal-cdn")
    assert "CDN up" in text
