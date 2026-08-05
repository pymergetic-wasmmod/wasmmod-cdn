"""CDN federation: prefix mounts, peers, grants (control plane)."""

from __future__ import annotations

from pymergetic.metal.cdn.services.federation.prefix import (
    longest_prefix_mount,
    name_under_prefix,
    normalize_mount_prefix,
)
from pymergetic.metal.cdn.services.federation.scopes import (
    KNOWN_SCOPES,
    SCOPE_FEDERATION_PUBLISH,
    SCOPE_FEDERATION_READ,
    key_allows,
    normalize_scopes,
    scopes_from_storage,
    scopes_permit_request,
    scopes_to_storage,
)

__all__ = [
    "FEDERATION_BOT_EMAIL",
    "KNOWN_SCOPES",
    "SCOPE_FEDERATION_PUBLISH",
    "SCOPE_FEDERATION_READ",
    "FederationRegistry",
    "key_allows",
    "longest_prefix_mount",
    "name_under_prefix",
    "normalize_mount_prefix",
    "normalize_scopes",
    "scopes_from_storage",
    "scopes_permit_request",
    "scopes_to_storage",
]


def __getattr__(name: str):
    if name in ("FederationRegistry", "FEDERATION_BOT_EMAIL"):
        from pymergetic.metal.cdn.services.federation import registry as _reg

        return getattr(_reg, name)
    raise AttributeError(name)
