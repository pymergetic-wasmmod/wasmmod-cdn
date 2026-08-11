"""Package-prefix matching for federation mounts."""

from __future__ import annotations

from typing import TypeVar

from pymergetic.wasmmod.cdn.layout import ChannelLayout

T = TypeVar("T")


def normalize_mount_prefix(prefix: str) -> str:
    """Validate and normalize a mount prefix (same grammar as package names)."""
    p = (prefix or "").strip().strip(".")
    if not p:
        raise ValueError("mount prefix must be non-empty")
    return ChannelLayout.validate_package_name(p)


def name_under_prefix(name: str, prefix: str) -> bool:
    """True if ``name`` equals ``prefix`` or is ``prefix.*``."""
    if name == prefix:
        return True
    return name.startswith(prefix + ".")


def longest_prefix_mount(
    name: str,
    mounts: list[tuple[str, T]],
) -> tuple[str, T] | None:
    """Return ``(prefix, value)`` for the longest matching enabled mount.

    ``mounts`` is a list of ``(prefix, payload)`` already filtered to enabled.
    """
    best: tuple[str, T] | None = None
    best_len = -1
    for prefix, payload in mounts:
        if name_under_prefix(name, prefix) and len(prefix) > best_len:
            best = (prefix, payload)
            best_len = len(prefix)
    return best
