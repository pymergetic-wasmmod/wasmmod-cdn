"""Infer package name / mount from artifact filenames."""

from __future__ import annotations

import re

from pymergetic.metal.cdn.layout import ChannelLayout
from pymergetic.metal.cdn.services.federation.prefix import longest_prefix_mount
from pymergetic.metal.cdn.services.federation.tables import (
    FederationDirection,
    FederationMountRead,
)

_AOT_TAIL = re.compile(r"\.[A-Za-z0-9_]+\.aot\d+$")


def artifact_package_hint(filename: str) -> str | None:
    """Best-effort package FQN from an artifact filename (``pkg.wasm``, …)."""
    name = filename.lstrip("/")
    if not name or "/" in name or name in (".", ".."):
        return None
    name = name.removesuffix(".zlib")
    if name.endswith(".wasm"):
        name = name[: -len(".wasm")]
    elif name.endswith(".elf"):
        name = name[: -len(".elf")]
    else:
        name = _AOT_TAIL.sub("", name)
    try:
        return ChannelLayout.validate_package_name(name)
    except ValueError:
        return None


def mount_for_artifact(
    filename: str, mounts: list[FederationMountRead]
) -> FederationMountRead | None:
    enabled = [
        (m.prefix, m)
        for m in mounts
        if m.enabled and m.direction == FederationDirection.PULL
    ]
    hint = artifact_package_hint(filename)
    if hint:
        hit = longest_prefix_mount(hint, enabled)
        if hit:
            return hit[1]
    for prefix, mount in sorted(enabled, key=lambda x: -len(x[0])):
        if filename == prefix or filename.startswith((prefix + ".", prefix + "/")):
            return mount
    return None
