"""Password and API-key crypto helpers."""

from __future__ import annotations

import hashlib
import secrets

import bcrypt

API_KEY_PREFIX = "mcdn"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def generate_api_key() -> tuple[str, str, str]:
    """Return (full_key, public_prefix, sha256_hex)."""
    public = secrets.token_hex(4)
    secret = secrets.token_urlsafe(32)
    full = f"{API_KEY_PREFIX}_{public}_{secret}"
    return full, public, hash_api_key(full)


def hash_api_key(full_key: str) -> str:
    return hashlib.sha256(full_key.encode("utf-8")).hexdigest()
