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
