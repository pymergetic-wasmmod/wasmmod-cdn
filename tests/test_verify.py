"""Signature verify policy + hexdump helpers."""

from __future__ import annotations

import struct
from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from pymergetic.wasmmod.cdn_client.format import hexdump
from pymergetic.wasmmod.cdn_client.verify import enforce_signed_policy, verify_artifact


def _uleb(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _custom_section(name: str, payload: bytes) -> bytes:
    name_b = name.encode()
    body = _uleb(len(name_b)) + name_b + payload
    return bytes([0]) + _uleb(len(body)) + body


def _minimal_wasm(*sections: bytes) -> bytes:
    return b"\x00asm\x01\x00\x00\x00" + b"".join(sections)


def _make_ca_and_leaf():
    now = datetime.now(UTC)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test-ca")])
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(ca_key, hashes.SHA256())
    )

    leaf_key = ec.generate_private_key(ec.SECP256R1())
    leaf_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test-leaf")])
    leaf_cert = (
        x509.CertificateBuilder()
        .subject_name(leaf_name)
        .issuer_name(ca_name)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=30))
        .sign(ca_key, hashes.SHA256())
    )
    return ca_cert, leaf_cert, leaf_key


def _mpws_sig(sig: bytes, chain: bytes) -> bytes:
    return (
        b"MPWS"
        + bytes([1, 0])
        + struct.pack(">H", len(sig))
        + sig
        + struct.pack(">H", len(chain))
        + chain
    )


def test_hexdump_basic() -> None:
    text = hexdump(b"hello\x00\xff", width=8)
    assert "00000000" in text
    assert "68 65 6c 6c 6f" in text


def test_enforce_off_and_present() -> None:
    bare = _minimal_wasm()
    assert enforce_signed_policy(bare, mode="off", trust_roots=[]) is None
    with pytest.raises(ValueError, match="wasmmod.sig required"):
        enforce_signed_policy(bare, mode="present", trust_roots=[], filename="x.wasm")


def test_verify_mpws_roundtrip() -> None:
    ca_cert, leaf_cert, leaf_key = _make_ca_and_leaf()
    body = _minimal_wasm(_custom_section("wasmmod.pack", b"MPWP" + b"\x00" * 8))
    sig = leaf_key.sign(body, ec.ECDSA(hashes.SHA256()))
    chain = leaf_cert.public_bytes(serialization.Encoding.DER)
    mpws = _mpws_sig(sig, chain)
    signed = body + _custom_section("wasmmod.sig", mpws)

    root_der = ca_cert.public_bytes(serialization.Encoding.DER)
    result = verify_artifact(signed, trust_roots=[root_der], filename="t.wasm")
    assert result.ok, result.error
    assert result.format == "mpws"

    enforce_signed_policy(signed, mode="verify", trust_roots=[root_der], filename="t.wasm")

    with pytest.raises(ValueError, match="verify failed"):
        enforce_signed_policy(
            signed,
            mode="verify",
            trust_roots=[b"not-a-cert"],
            filename="t.wasm",
        )


def test_verify_rejects_unsigned_in_verify_mode() -> None:
    bare = _minimal_wasm()
    with pytest.raises(ValueError, match="verify failed|no wasmmod.sig"):
        enforce_signed_policy(bare, mode="verify", trust_roots=[b"x"], filename="u.wasm")
