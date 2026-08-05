"""Federation scopes for API keys (machine credentials)."""

from __future__ import annotations

import json
from collections.abc import Iterable

# Explicit scopes — empty scopes on a key means unrestricted (legacy keys).
SCOPE_FEDERATION_READ = "federation:read"
SCOPE_FEDERATION_PUBLISH = "federation:publish"

KNOWN_SCOPES = frozenset({SCOPE_FEDERATION_READ, SCOPE_FEDERATION_PUBLISH})


def normalize_scopes(scopes: Iterable[str] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in scopes or ():
        s = str(raw).strip().lower()
        if not s or s in seen:
            continue
        if s not in KNOWN_SCOPES:
            raise ValueError(f"unknown API key scope: {s}")
        seen.add(s)
        out.append(s)
    return out


def scopes_to_storage(scopes: Iterable[str] | None) -> str:
    return json.dumps(normalize_scopes(scopes), separators=(",", ":"))


def scopes_from_storage(raw: str | None) -> list[str]:
    if not raw or not str(raw).strip():
        return []
    text = str(raw).strip()
    if text.startswith("["):
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError("invalid scopes JSON on api key") from exc
        if not isinstance(data, list):
            raise ValueError("scopes must be a JSON list")
        return normalize_scopes(str(x) for x in data)
    # space / comma separated fallback
    parts = [p for p in text.replace(",", " ").split() if p]
    return normalize_scopes(parts)


def key_allows(scopes: Iterable[str] | None, needed: str) -> bool:
    """Empty scopes = unrestricted (backward compatible)."""
    have = list(scopes or [])
    if not have:
        return True
    return needed in have


def _strip_base(path: str, base_path: str | None) -> str:
    p = path or "/"
    if not p.startswith("/"):
        p = f"/{p}"
    prefix = (base_path or "").rstrip("/")
    if prefix and prefix != "/" and p.startswith(prefix):
        p = p[len(prefix) :] or "/"
    return p


def _is_federation_read_path(path: str) -> bool:
    """Paths a ``federation:read`` key may GET/HEAD."""
    if path in ("/health", "/status", "/ready", "/auth/me"):
        return True
    if path == "/federation/mounts" or path.startswith("/federation/mounts?"):
        return True
    for prefix in ("/packages", "/artifacts/", "/index/"):
        if path == prefix.rstrip("/") or path.startswith(prefix):
            return True
    return False


def scopes_permit_request(
    scopes: Iterable[str] | None,
    *,
    method: str,
    path: str,
    base_path: str | None = None,
) -> bool:
    """Whether a scoped API key may call ``method`` ``path``.

    Empty scopes remain unrestricted. Session auth never calls this.
    """
    have = list(scopes or [])
    if not have:
        return True
    method_u = method.upper()
    app_path = _strip_base(path, base_path)
    can_read = SCOPE_FEDERATION_READ in have or SCOPE_FEDERATION_PUBLISH in have
    can_publish = SCOPE_FEDERATION_PUBLISH in have

    if method_u in ("GET", "HEAD"):
        return can_read and _is_federation_read_path(app_path)

    if method_u == "POST" and (
        app_path == "/publish" or app_path.startswith("/publish?")
    ):
        return can_publish

    return False
