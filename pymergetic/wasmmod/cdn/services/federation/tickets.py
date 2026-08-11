"""Ed25519 federation tickets (``Authorization: MetalFed …``)."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

SCHEME = "MetalFed"
TICKET_VERSION = 1
DEFAULT_TTL_S = 60


def _b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64u_decode(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def kid_for_public(public_raw: bytes) -> str:
    return hashlib.sha256(public_raw).hexdigest()[:16]


@dataclass(frozen=True)
class FedKeyPair:
    private_b64: str
    public_b64: str
    key_id: str


def generate_keypair() -> FedKeyPair:
    priv = Ed25519PrivateKey.generate()
    private_raw = priv.private_bytes_raw()
    public_raw = priv.public_key().public_bytes_raw()
    return FedKeyPair(
        private_b64=_b64u(private_raw),
        public_b64=_b64u(public_raw),
        key_id=kid_for_public(public_raw),
    )


def _load_private(private_b64: str) -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(_b64u_decode(private_b64.strip()))


def _load_public(public_b64: str) -> Ed25519PublicKey:
    return Ed25519PublicKey.from_public_bytes(_b64u_decode(public_b64.strip()))


def public_from_private(private_b64: str) -> tuple[str, str]:
    """Return ``(public_b64, key_id)`` for a private seed."""
    priv = _load_private(private_b64)
    public_raw = priv.public_key().public_bytes_raw()
    return _b64u(public_raw), kid_for_public(public_raw)


def public_key_id(public_b64: str) -> str:
    return kid_for_public(_b64u_decode(public_b64.strip()))


@dataclass(frozen=True)
class TicketClaims:
    prefix: str
    exp: int
    jti: str
    aud: str | None
    hop: int
    scopes: list[str]
    kid: str
    version: int = TICKET_VERSION

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "v": self.version,
            "kid": self.kid,
            "prefix": self.prefix,
            "exp": self.exp,
            "jti": self.jti,
            "hop": self.hop,
            "scopes": self.scopes,
        }
        if self.aud:
            out["aud"] = self.aud
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TicketClaims:
        scopes_raw = data.get("scopes") or []
        if not isinstance(scopes_raw, list):
            raise ValueError("scopes must be a list")
        return cls(
            version=int(data.get("v") or TICKET_VERSION),
            kid=str(data.get("kid") or ""),
            prefix=str(data.get("prefix") or ""),
            exp=int(data["exp"]),
            jti=str(data.get("jti") or ""),
            aud=(str(data["aud"]) if data.get("aud") else None),
            hop=int(data.get("hop") or 0),
            scopes=[str(s) for s in scopes_raw],
        )


def sign_ticket(
    private_b64: str,
    *,
    prefix: str,
    scopes: list[str],
    hop: int = 0,
    aud: str | None = None,
    ttl_s: int = DEFAULT_TTL_S,
    key_id: str | None = None,
) -> str:
    """Return ``MetalFed <payload>.<sig>`` (payload/sig are base64url)."""
    priv = _load_private(private_b64)
    pub_raw = priv.public_key().public_bytes_raw()
    kid = key_id or kid_for_public(pub_raw)
    claims = TicketClaims(
        kid=kid,
        prefix=prefix,
        exp=int(time.time()) + max(5, ttl_s),
        jti=secrets.token_hex(12),
        aud=aud,
        hop=hop,
        scopes=list(scopes),
    )
    payload = _b64u(json.dumps(claims.to_dict(), separators=(",", ":")).encode("utf-8"))
    sig = _b64u(priv.sign(payload.encode("ascii")))
    return f"{SCHEME} {payload}.{sig}"


def parse_authorization(header: str | None) -> str | None:
    """Return raw ``payload.sig`` if header is MetalFed, else None."""
    if not header:
        return None
    parts = header.strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() != SCHEME.lower():
        return None
    return parts[1].strip()


def verify_ticket(public_b64: str, token: str, *, now: int | None = None) -> TicketClaims:
    """Verify ``payload.sig`` (without scheme) against a public key."""
    if "." not in token:
        raise ValueError("malformed federation ticket")
    payload, sig = token.rsplit(".", 1)
    pub = _load_public(public_b64)
    try:
        pub.verify(_b64u_decode(sig), payload.encode("ascii"))
    except InvalidSignature as exc:
        raise ValueError("invalid federation ticket signature") from exc
    try:
        data = json.loads(_b64u_decode(payload).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid federation ticket payload") from exc
    if not isinstance(data, dict):
        raise ValueError("invalid federation ticket payload")
    claims = TicketClaims.from_dict(data)
    if not claims.prefix or not claims.kid:
        raise ValueError("federation ticket missing prefix/kid")
    ts = int(time.time()) if now is None else now
    if claims.exp < ts:
        raise ValueError("federation ticket expired")
    expected_kid = public_key_id(public_b64)
    if claims.kid != expected_kid:
        raise ValueError("federation ticket kid mismatch")
    return claims
