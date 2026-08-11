"""Path prefix helpers for reverse-proxy / subroute mounts."""

from __future__ import annotations

from urllib.parse import quote


def normalize_base_path(raw: str) -> str:
    """Return ``/`` (site root) or ``/cdn``-style prefix (no trailing slash)."""
    value = (raw or "/").strip() or "/"
    if not value.startswith("/"):
        value = f"/{value}"
    if value != "/":
        value = value.rstrip("/")
    return value


def path_prefix(base_path: str) -> str:
    """ASGI mount prefix: empty string at site root, else ``/cdn``."""
    normalized = normalize_base_path(base_path)
    return "" if normalized == "/" else normalized


def join_base(base_path: str, *parts: str) -> str:
    """Join ``base_path`` with absolute app paths (``/channels/lead``)."""
    prefix = path_prefix(base_path)
    path = "/".join(p.strip("/") for p in parts if p and p != "/")
    if not path:
        return prefix or "/"
    return f"{prefix}/{path}"


def channel_path(channel: str) -> str:
    if channel == "lead":
        return "/channels/lead"
    version = channel.removeprefix("@")
    return f"/channels/pin/{quote(version, safe='')}"


def package_path(channel: str, name: str) -> str:
    # Keep ``/`` for scoped ``org/pkg`` names (path segments).
    return f"{channel_path(channel)}/packs/{quote(name, safe='/')}"


def author_path(email: str) -> str:
    return f"/authors/{quote(email.strip(), safe='')}"
