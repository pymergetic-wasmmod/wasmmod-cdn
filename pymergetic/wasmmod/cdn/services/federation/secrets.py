"""Encrypt federation bearer tokens at rest (Fernet + HKDF)."""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


def _fernet(secret: str) -> Fernet:
    material = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"wasmmod-cdn-federation-v1",
        info=b"federation-credential",
    ).derive(secret.encode("utf-8"))
    return Fernet(base64.urlsafe_b64encode(material))


def encrypt_secret(plaintext: str, *, secret_key: str) -> str:
    if not plaintext:
        raise ValueError("credential plaintext must be non-empty")
    if not secret_key:
        raise ValueError("federation secrets key is empty")
    token = _fernet(secret_key).encrypt(plaintext.encode("utf-8"))
    return token.decode("ascii")


def decrypt_secret(ciphertext: str, *, secret_key: str) -> str:
    if not ciphertext:
        raise ValueError("credential ciphertext must be non-empty")
    if not secret_key:
        raise ValueError("federation secrets key is empty")
    try:
        return _fernet(secret_key).decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("cannot decrypt federation credential") from exc


def secret_fingerprint(plaintext: str) -> str:
    """Non-reversible hint for UI (first 12 hex of sha256)."""
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()[:12]
