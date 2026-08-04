"""Compatibility re-export — prefer ``pymergetic.metal.cdn_client``."""

from __future__ import annotations

from pymergetic.metal.cdn_client import (
    TOKEN_SOURCE_API_KEY,
    TOKEN_SOURCE_OIDC,
    ArtifactDownload,
    CdnClient,
    ClientError,
    clear_token,
    config_dir,
    config_path,
    load_config,
    save_config,
    token_source,
)

__all__ = [
    "TOKEN_SOURCE_API_KEY",
    "TOKEN_SOURCE_OIDC",
    "ArtifactDownload",
    "CdnClient",
    "ClientError",
    "clear_token",
    "config_dir",
    "config_path",
    "load_config",
    "save_config",
    "token_source",
]
