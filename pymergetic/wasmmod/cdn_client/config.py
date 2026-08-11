"""Local CLI/client config (`~/.config/wasmmod-cdn/config.json`)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal, TypedDict

# Bearer tokens are the stable auth contract. Future OIDC/passkeys mint or
# refresh a Bearer token; publish/claim paths stay token-agnostic.
TokenSource = Literal["api_key", "oidc"]

TOKEN_SOURCE_API_KEY: TokenSource = "api_key"
TOKEN_SOURCE_OIDC: TokenSource = "oidc"  # prepared; not implemented yet


class ClientConfig(TypedDict, total=False):
    """Stored client credentials / preferences."""

    url: str
    token: str
    email: str
    token_source: TokenSource


def config_dir() -> Path:
    return Path.home() / ".config" / "wasmmod-cdn"


def config_path() -> Path:
    return config_dir() / "config.json"


def load_config() -> dict[str, Any]:
    path = config_path()
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {}
    return data


def save_config(data: dict[str, Any]) -> Path:
    """Write config; defaults ``token_source`` to ``api_key`` when a token is set."""
    payload = dict(data)
    if payload.get("token") and "token_source" not in payload:
        payload["token_source"] = TOKEN_SOURCE_API_KEY
    root = config_dir()
    root.mkdir(parents=True, exist_ok=True)
    path = config_path()
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path


def clear_token() -> None:
    """Remove stored Bearer token (keeps url/email when present)."""
    cfg = load_config()
    cfg.pop("token", None)
    cfg.pop("token_source", None)
    if cfg:
        save_config(cfg)
        return
    path = config_path()
    if path.is_file():
        path.unlink()


def token_source(cfg: dict[str, Any] | None = None) -> TokenSource | None:
    data = load_config() if cfg is None else cfg
    raw = data.get("token_source")
    if raw in ("api_key", "oidc"):
        return raw  # type: ignore[return-value]
    if data.get("token"):
        return TOKEN_SOURCE_API_KEY
    return None
