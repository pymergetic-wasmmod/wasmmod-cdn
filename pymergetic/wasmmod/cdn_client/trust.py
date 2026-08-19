"""MPTB trust-bundle parsing and sub-CA allow/deny policy gate.

Python twin of the device-side parser in
``extmod/wasmmod/src/pymergetic/wasmmod/verify/__impl__.c``. The privileged
operation (applying a bundle) stays on a device/CDN that holds the trust
roots; this module only *parses* the bundle wire format and applies the
allow/deny decision to an already-verified certificate chain, so any seat can
agree on what the bundle says.

The MPTB wire format (big-endian, see sign.py `trust-bundle` for the builder):

  "MPTB"   4 bytes
  version  u16 = 1
  type     u16 = 1 (TRUST)
  issued   u64 unix secs
  expires  u64 unix secs
  n_allow  u16
  n_deny   u16
  allow    n_allow * 32-byte SHA-256 sub-CA fingerprints
  deny     n_deny  * 32-byte SHA-256 sub-CA fingerprints
  sig_len  u16,  sig (ECDSA-P256 raw r||s)
  chain_len u32, chain (DER certs, leaf-first)

The signature covers every byte up to (excluding) the sig_len field. A bundle's
sub-CA fingerprints are SHA-256 of the DER *sub-CA* cert that issued a pack's
leaf (fingerprint convention matches the CDN TrustRoot.sha256 field).
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass, field

MPTB_MAGIC = b"MPTB"
MPTB_VER = 1
MPTB_TYPE_TRUST = 1
FP_LEN = 32


class MptbError(ValueError):
    """Malformed or unsupported MPTB bundle."""


@dataclass(frozen=True)
class MptbBundle:
    issued: int
    expires: int
    allow: tuple[bytes, ...]  # one 32-byte SHA-256 per entry
    deny: tuple[bytes, ...]
    # Raw fields (fingerprints of the signer leaf + chain) kept for diagnostics;
    # the privileged apply path verifies these against the trust roots.
    sig: bytes = b""
    chain: bytes = b""
    covered_len: int = 0

    def is_expired(self, now: int | None = None) -> bool:
        now = now if now is not None else _unix_now()
        return now >= self.expires

    def policy(self) -> SubcaPolicy:
        return SubcaPolicy(allow=self.allow, deny=self.deny)


def _unix_now() -> int:
    import time

    return int(time.time())


def parse_mptb(data: bytes) -> MptbBundle:
    """Parse an MPTB envelope (no crypto). Raises MptbError on malformed data."""
    if len(data) < 28 or not data.startswith(MPTB_MAGIC):
        raise MptbError("not an MPTB (trust bundle)")
    off = 4
    (ver,) = struct.unpack_from(">H", data, off)
    off += 2
    (typ,) = struct.unpack_from(">H", data, off)
    off += 2
    if ver != MPTB_VER or typ != MPTB_TYPE_TRUST:
        raise MptbError(f"unsupported MPTB version/type ({ver}/{typ})")
    (issued,) = struct.unpack_from(">Q", data, off)
    off += 8
    (expires,) = struct.unpack_from(">Q", data, off)
    off += 8
    (n_allow,) = struct.unpack_from(">H", data, off)
    off += 2
    (n_deny,) = struct.unpack_from(">H", data, off)
    off += 2
    if off + (n_allow + n_deny) * FP_LEN + 6 > len(data):
        raise MptbError("truncated MPTB")

    allow = tuple(
        data[off + i * FP_LEN : off + (i + 1) * FP_LEN] for i in range(n_allow)
    )
    off += n_allow * FP_LEN
    deny = tuple(data[off + i * FP_LEN : off + (i + 1) * FP_LEN] for i in range(n_deny))
    off += n_deny * FP_LEN

    covered = off
    if off + 2 > len(data):
        raise MptbError("missing MPTB signature")
    (sig_len,) = struct.unpack_from(">H", data, off)
    off += 2
    if sig_len == 0 or off + sig_len > len(data):
        raise MptbError("bad MPTB signature length")
    sig = data[off : off + sig_len]
    off += sig_len
    if off + 4 > len(data):
        raise MptbError("missing MPTB signer chain")
    (chain_len,) = struct.unpack_from(">I", data, off)
    off += 4
    if chain_len == 0 or off + chain_len != len(data):
        raise MptbError("bad MPTB signer chain length")
    chain = data[off : off + chain_len]

    return MptbBundle(
        issued=issued,
        expires=expires,
        allow=allow,
        deny=deny,
        sig=sig,
        chain=chain,
        covered_len=covered,
    )


@dataclass(frozen=True)
class SubcaPolicy:
    """Allow/deny lists of sub-CA SHA-256 fingerprints.

    Rule (mirrors the device gate):
      - empty policy (no allow and no deny) => allow everything
      - a denied sub-CA always fails
      - a non-empty allow list must contain the sub-CA
    """

    allow: tuple[bytes, ...] = ()
    deny: tuple[bytes, ...] = ()

    @classmethod
    def active(
        cls,
        allow: tuple[bytes, ...] = (),
        deny: tuple[bytes, ...] = (),
    ) -> "SubcaPolicy":
        return cls(allow=tuple(allow), deny=tuple(deny))

    def allows(self, subca_der: bytes | None) -> bool:
        """True if the given sub-CA cert (DER) is permitted under this policy."""
        if not self.allow and not self.deny:
            return True
        if subca_der is None:
            # An enforcing policy with no recoverable sub-CA fails closed.
            return False
        fp = hashlib.sha256(subca_der).digest()
        return fp not in self.deny and (not self.allow or fp in self.allow)


def subca_of_chain(certs: tuple[bytes, ...]) -> bytes | None:
    """Return the DER of the cert that issued the leaf (first intermediate).

    Matches the device gate: the pack's *issuing sub-CA* is the first cert after
    the leaf in the MPWS chain. Returns None if the leaf chains straight to a
    root (no intermediate), in which case an enforcing policy rejects it.
    """
    if len(certs) < 2:
        return None
    return certs[1]
