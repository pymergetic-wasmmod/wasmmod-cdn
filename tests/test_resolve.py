"""Exact-deps install order + scoped names."""

from datetime import UTC, datetime

import pytest

from pymergetic.wasmmod.cdn.layout import ChannelLayout
from pymergetic.wasmmod.cdn.models import ChannelIndex, PackageEntry
from pymergetic.wasmmod.cdn.resolve import parse_root, resolve_install_order
from pymergetic.wasmmod.cdn.services.channel import sign_index, verify_index_signature


def test_scoped_package_names() -> None:
    assert ChannelLayout.validate_package_name("hello") == "hello"
    assert ChannelLayout.validate_package_name("org/pkg") == "org/pkg"
    assert ChannelLayout.validate_package_name("test_a") == "test_a"
    assert (
        ChannelLayout.validate_package_name("test_a.test_b.test_c")
        == "test_a.test_b.test_c"
    )
    assert ChannelLayout.validate_package_name("test_a.test_d") == "test_a.test_d"
    with pytest.raises(ValueError):
        ChannelLayout.validate_package_name("a/b/c")
    with pytest.raises(ValueError):
        ChannelLayout.validate_package_name("bad-name")
    with pytest.raises(ValueError):
        ChannelLayout.validate_package_name("a.b/c")


def test_parse_root() -> None:
    assert parse_root("hello") == ("hello", None)
    assert parse_root("org/pkg@1.0.0") == ("org/pkg", "1.0.0")


def test_resolve_install_order() -> None:
    now = datetime.now(UTC)
    lead = ChannelIndex(
        schema=1,
        channel="lead",
        generated=now,
        packages={
            "app": PackageEntry(version="1.0.0", deps={"lib": "0.1.0"}),
            "lib": PackageEntry(version="0.1.0", deps={}),
        },
    )
    order = resolve_install_order("app", lead=lead)
    assert order == [("lib", "0.1.0"), ("app", "1.0.0")]


def test_resolve_pin_root() -> None:
    now = datetime.now(UTC)
    lead = ChannelIndex(schema=1, channel="lead", generated=now, packages={})
    pin = ChannelIndex(
        schema=1,
        channel="@0.2.0",
        generated=now,
        packages={"tool": PackageEntry(version="0.2.0", deps={})},
    )
    order = resolve_install_order("tool@0.2.0", lead=lead, pins={"0.2.0": pin})
    assert order == [("tool", "0.2.0")]


def test_index_signature() -> None:
    now = datetime.now(UTC)
    index = ChannelIndex(schema=1, channel="lead", generated=now, packages={})
    sig = sign_index(index, "secret")
    signed = index.model_copy(update={"signature": sig})
    assert verify_index_signature(signed, "secret")
    assert not verify_index_signature(signed, "wrong")
