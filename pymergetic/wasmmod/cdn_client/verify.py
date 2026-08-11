"""MPWS / ECDSA verification for CDN publish policy and optional tooling.

Uses ``cryptography``. Offline wasmmod keeps openssl via ``wasmmod sign verify``.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal

from pymergetic.wasmmod.cdn_client.contents import (
    MPWS_MAGIC,
    MPWS_VER,
    SIG_SECTION,
    extract_custom_section,
    split_der_certs,
    unwrap_mpzl,
    without_sig_section,
)

RequireSignedMode = Literal["off", "present", "verify"]


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    error: str | None = None
    signed: bool = False
    format: str | None = None
    leaf_sha256: str | None = None


def _crypto():
    try:
        from cryptography import x509
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
    except ImportError as exc:
        raise ImportError(
            "verify requires cryptography (pip install 'cryptography>=42')"
        ) from exc
    return x509, InvalidSignature, hashes, serialization, ec, padding, rsa


def _parse_sig_chain(payload: bytes) -> tuple[str, bytes, bytes]:
    if len(payload) >= 8 and payload[:4] == MPWS_MAGIC and payload[4] == MPWS_VER:
        sl = int.from_bytes(payload[6:8], "big")
        if sl == 0 or 8 + sl > len(payload):
            raise ValueError("bad MPWS sig length")
        sig = payload[8 : 8 + sl]
        rest = payload[8 + sl :]
        chain = b""
        if len(rest) >= 2:
            cl = int.from_bytes(rest[0:2], "big")
            if 2 + cl > len(rest):
                raise ValueError("bad MPWS chain length")
            chain = rest[2 : 2 + cl]
        return "mpws", sig, chain
    if not payload:
        raise ValueError("empty wasmmod.sig")
    return "raw", payload, b""


def _aot_align4(buf: bytes) -> bytes:
    pad = (-len(buf)) % 4
    return buf if pad == 0 else buf + (b"\x00" * pad)


def _load_roots(blobs: list[bytes], x509):
    roots = []
    for blob in blobs:
        if b"BEGIN CERTIFICATE" in blob:
            roots.append(x509.load_pem_x509_certificate(blob))
        else:
            roots.append(x509.load_der_x509_certificate(blob))
    return roots


def _check_issued(cert, issuer, *, hashes, InvalidSignature, ec, rsa, padding) -> None:
    pub = issuer.public_key()
    algo = cert.signature_hash_algorithm
    if algo is None:
        raise ValueError("certificate missing signature hash algorithm")
    if isinstance(pub, rsa.RSAPublicKey):
        pub.verify(cert.signature, cert.tbs_certificate_bytes, padding.PKCS1v15(), algo)
    elif isinstance(pub, ec.EllipticCurvePublicKey):
        pub.verify(cert.signature, cert.tbs_certificate_bytes, ec.ECDSA(algo))
    else:
        raise ValueError("unsupported issuer key type")


def _chain_to_roots(certs, roots, *, hashes, InvalidSignature, ec, rsa, padding) -> None:
    if not certs:
        raise ValueError("empty certificate chain")
    for i in range(len(certs) - 1):
        child, parent = certs[i], certs[i + 1]
        if child.issuer != parent.subject:
            raise ValueError(f"chain break at cert[{i}]: issuer/subject mismatch")
        try:
            _check_issued(
                child, parent, hashes=hashes, InvalidSignature=InvalidSignature, ec=ec, rsa=rsa, padding=padding
            )
        except InvalidSignature as exc:
            raise ValueError(f"chain break at cert[{i}]: bad signature") from exc

    last = certs[-1]
    for root in roots:
        if last.issuer == root.subject or last.subject == root.subject:
            try:
                if last.subject == root.subject and last.issuer == root.subject or last.issuer == root.subject:
                    _check_issued(
                        last, root, hashes=hashes, InvalidSignature=InvalidSignature, ec=ec, rsa=rsa, padding=padding
                    )
                return
            except InvalidSignature:
                continue
        # last cert IS the trust root
        if last.fingerprint(hashes.SHA256()) == root.fingerprint(hashes.SHA256()):
            return
    raise ValueError("chain does not reach a configured trust root")


def verify_artifact(
    data: bytes,
    *,
    trust_roots: list[bytes],
    filename: str = "",
) -> VerifyResult:
    """Verify embedded ``wasmmod.sig`` against DER/PEM trust roots."""
    del filename
    x509, InvalidSignature, hashes, serialization, ec, padding, rsa = _crypto()
    try:
        naked = unwrap_mpzl(data)
    except ValueError as exc:
        return VerifyResult(ok=False, error=f"unwrap failed: {exc}")

    payload = extract_custom_section(naked, SIG_SECTION)
    if payload is None:
        return VerifyResult(ok=False, signed=False, error=f"no {SIG_SECTION} section")

    try:
        fmt, sig, chain = _parse_sig_chain(payload)
    except ValueError as exc:
        return VerifyResult(ok=False, signed=True, error=str(exc))

    try:
        stripped = without_sig_section(naked)
    except ValueError as exc:
        return VerifyResult(ok=False, signed=True, error=str(exc))
    if stripped[:4] == b"\x00aot":
        stripped = _aot_align4(stripped)

    if not chain:
        return VerifyResult(ok=False, signed=True, format=fmt, error="empty MPWS chain")
    if not trust_roots:
        return VerifyResult(ok=False, signed=True, format=fmt, error="no trust roots configured")

    try:
        certs = [x509.load_der_x509_certificate(c) for c in split_der_certs(chain)]
        roots = _load_roots(trust_roots, x509)
    except Exception as exc:
        return VerifyResult(ok=False, signed=True, format=fmt, error=f"cert parse failed: {exc}")

    leaf = certs[0]
    leaf_fp = hashlib.sha256(leaf.public_bytes(serialization.Encoding.DER)).hexdigest()

    try:
        _chain_to_roots(
            certs, roots, hashes=hashes, InvalidSignature=InvalidSignature, ec=ec, rsa=rsa, padding=padding
        )
    except ValueError as exc:
        return VerifyResult(ok=False, signed=True, format=fmt, leaf_sha256=leaf_fp, error=str(exc))

    pub = leaf.public_key()
    if not isinstance(pub, ec.EllipticCurvePublicKey):
        return VerifyResult(
            ok=False, signed=True, format=fmt, leaf_sha256=leaf_fp, error="leaf is not ECDSA"
        )
    try:
        pub.verify(sig, stripped, ec.ECDSA(hashes.SHA256()))
    except InvalidSignature:
        return VerifyResult(
            ok=False, signed=True, format=fmt, leaf_sha256=leaf_fp, error="ECDSA signature mismatch"
        )
    except Exception as exc:
        return VerifyResult(
            ok=False, signed=True, format=fmt, leaf_sha256=leaf_fp, error=f"ECDSA verify failed: {exc}"
        )

    return VerifyResult(ok=True, signed=True, format=fmt, leaf_sha256=leaf_fp)


def enforce_signed_policy(
    data: bytes,
    *,
    mode: RequireSignedMode,
    trust_roots: list[bytes],
    filename: str = "",
) -> VerifyResult | None:
    """Apply ``off`` / ``present`` / ``verify``. Raises ``ValueError`` on reject."""
    if mode == "off":
        return None
    naked = unwrap_mpzl(data)
    label = filename or "artifact"
    # UEFI PE/COFF (BOOTX64.EFI) cannot carry wasmmod.sig — freestanding firmware
    # exception (iPXE chains the PE; guest packs stay ELF64/wasm-signed).
    if len(naked) >= 2 and naked[:2] == b"MZ":
        return VerifyResult(ok=True, signed=False, format="pe")
    # Emscripten .mjs loader — text/JS, not a wasmmod container.
    low = (filename or "").lower()
    if low.endswith(".mjs") or low.endswith(".mjs.zlib"):
        return VerifyResult(ok=True, signed=False, format="mjs")
    has = extract_custom_section(naked, SIG_SECTION) is not None
    if mode == "present":
        if not has:
            raise ValueError(f"{label}: wasmmod.sig required (REQUIRE_SIGNED=present)")
        return VerifyResult(ok=True, signed=True)
    if mode == "verify":
        result = verify_artifact(data, trust_roots=trust_roots, filename=filename)
        if not result.ok:
            raise ValueError(f"{label}: signature verify failed: {result.error}")
        return result
    raise ValueError(f"unknown REQUIRE_SIGNED mode: {mode!r}")
