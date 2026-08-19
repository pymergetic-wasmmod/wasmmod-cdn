"""Trust bundle (MPTB) parsing + sub-CA allow/deny publish gate.

Covers ``cdn_client/trust.py`` (parse + policy gate) and the publish-time
``verify_artifact``/``enforce_signed_policy`` gate, mirroring the device.
"""

from __future__ import annotations

import hashlib
import struct
from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from pymergetic.wasmmod.cdn_client.contents import (
    SIG_SECTION,
    extract_custom_section,
    split_der_certs,
)
from pymergetic.wasmmod.cdn_client.trust import (
    FP_LEN,
    SubcaPolicy,
    parse_mptb,
    subca_of_chain,
)
from pymergetic.wasmmod.cdn_client.verify import enforce_signed_policy, verify_artifact

MPTB = b"MPTB"
_DUMMY_SIG = b"\x30" * 70  # opaque placeholder sig payload
_DUMMY_CHAIN = b"\x30\x02\x01\x00"


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


def build_mptb(
    allow=None,
    deny=None,
    *,
    issued=1000000,
    expires=2000000000,
    sig=None,
    chain=None,
) -> bytes:
    """Build a well-formed MPTB envelope (parse-only; signature is a placeholder)."""
    allow = allow or []
    deny = deny or []
    out = bytearray(MPTB)
    out += struct.pack(">HH", 1, 1)
    out += struct.pack(">QQ", issued, expires)
    out += struct.pack(">HH", len(allow), len(deny))
    for fp in allow:
        out += fp
    for fp in deny:
        out += fp
    covered = len(out)
    sig = _DUMMY_SIG if sig is None else sig
    chain = _DUMMY_CHAIN if chain is None else chain
    out += struct.pack(">H", len(sig)) + sig
    out += struct.pack(">I", len(chain)) + chain
    return bytes(out)


def _make_pki():
    """root → sub-CA → leaf. Returns (root_der, sub_der, leaf_chain_der)."""
    now = datetime.now(UTC)

    root_key = ec.generate_private_key(ec.SECP256R1())
    root_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "trust-root")])
    root_cert = (
        x509.CertificateBuilder()
        .subject_name(root_name)
        .issuer_name(root_name)
        .public_key(root_key.public_key())
        .serial_number(0x1111)
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(root_key, hashes.SHA256())
    )

    sub_key = ec.generate_private_key(ec.SECP256R1())
    sub_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test-sub")])
    sub_cert = (
        x509.CertificateBuilder()
        .subject_name(sub_name)
        .issuer_name(root_cert.subject)
        .public_key(sub_key.public_key())
        .serial_number(0x1112)
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(root_key, hashes.SHA256())
    )

    leaf_key = ec.generate_private_key(ec.SECP256R1())
    leaf_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test-leaf")])
    leaf_cert = (
        x509.CertificateBuilder()
        .subject_name(leaf_name)
        .issuer_name(sub_cert.subject)
        .public_key(leaf_key.public_key())
        .serial_number(0x1113)
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=30))
        .sign(sub_key, hashes.SHA256())
    )

    return (
        root_cert.public_bytes(serialization.Encoding.DER),
        sub_cert.public_bytes(serialization.Encoding.DER),
        leaf_cert.public_bytes(serialization.Encoding.DER)
        + sub_cert.public_bytes(serialization.Encoding.DER)
        + root_cert.public_bytes(serialization.Encoding.DER),
        leaf_cert,
        leaf_key,
    )


def _mpws_sig(sig: bytes, chain: bytes) -> bytes:
    return (
        b"MPWS"
        + bytes([1, 0])
        + struct.pack(">H", len(sig))
        + sig
        + struct.pack(">H", len(chain))
        + chain
    )


# ---------------------------------------------------------------------------
# parse_mptb
# ---------------------------------------------------------------------------


def test_parse_mptb_decodes_lists() -> None:
    a = bytes(range(FP_LEN))
    d = bytes(range(64, 64 + FP_LEN))
    blob = build_mptb(allow=[a], deny=[d], issued=11, expires=22)
    parsed = parse_mptb(blob)
    assert parsed.issued == 11
    assert parsed.expires == 22
    assert parsed.allow == (a,)
    assert parsed.deny == (d,)
    assert parsed.covered_len == 4 + 2 + 2 + 8 + 8 + 2 + 2 + FP_LEN + FP_LEN


def test_parse_mptb_rejects_garbage() -> None:
    with pytest.raises(ValueError, match="not an MPTB"):
        parse_mptb(b"not-an-mptb")


# ---------------------------------------------------------------------------
# SubcaPolicy gate (feed real DER certs, as production does)
# ---------------------------------------------------------------------------


def test_subca_policy_gate() -> None:
    _root_der, sub_der, _chain, _lc, _lk = _make_pki()
    fp = hashlib.sha256(sub_der).digest()

    # empty policy -> allow everything (identity only)
    assert SubcaPolicy().allows(sub_der)

    # deny wins
    assert not SubcaPolicy(deny=(fp,)).allows(sub_der)

    # allow list non-empty -> must be listed
    assert SubcaPolicy(allow=(fp,)).allows(sub_der)
    assert not SubcaPolicy(allow=(bytes(FP_LEN),)).allows(sub_der)

    # no recoverable sub-CA -> fail closed under an enforcing policy
    assert not SubcaPolicy(allow=(fp,)).allows(None)
    assert not SubcaPolicy(deny=(fp,)).allows(None)
    assert SubcaPolicy().allows(None)


# ---------------------------------------------------------------------------
# Publish-time gate (verify_artifact / enforce_signed_policy)
# ---------------------------------------------------------------------------


def _signed_pack():
    root_der, sub_der, chain, leaf_cert, leaf_key = _make_pki()
    body = _minimal_wasm(_custom_section("wasmmod.pack", b"MPWP" + b"\x00" * 8))
    sig = leaf_key.sign(body, ec.ECDSA(hashes.SHA256()))
    pack = body + _custom_section("wasmmod.sig", _mpws_sig(sig, chain))
    return pack, root_der, sub_der, chain


def test_publish_gate_allow_and_deny() -> None:
    pack, root_der, sub_der, _chain = _signed_pack()
    fp_sub = hashlib.sha256(sub_der).digest()

    # Roots only (no policy) -> accepted.
    assert verify_artifact(pack, trust_roots=[root_der]).ok

    # Enforcing: sub-CA allowed -> accepted.
    allowed = SubcaPolicy(allow=(fp_sub,))
    assert verify_artifact(pack, trust_roots=[root_der], subca_policy=allowed).ok
    enforced = enforce_signed_policy(
        pack, mode="verify", trust_roots=[root_der], subca_policy=allowed, filename="p.wasm"
    )
    assert enforced is not None and enforced.ok

    # Enforcing: sub-CA denied -> rejected.
    denied = SubcaPolicy(deny=(fp_sub,))
    res = verify_artifact(pack, trust_roots=[root_der], subca_policy=denied)
    assert not res.ok
    with pytest.raises(ValueError, match="sub-CA denied"):
        enforce_signed_policy(
            pack, mode="verify", trust_roots=[root_der], subca_policy=denied, filename="p.wasm"
        )

    # Enforcing: allow list without this sub-CA -> rejected (not allowlisted).
    notlisted = SubcaPolicy(allow=(bytes(FP_LEN),))
    assert not verify_artifact(pack, trust_roots=[root_der], subca_policy=notlisted).ok


def test_subca_of_chain_helpers() -> None:
    pack, _root_der, sub_der, chain = _signed_pack()
    payload = extract_custom_section(pack, SIG_SECTION)
    assert payload is not None, "signed pack must embed a wasmmod.sig section"
    # Parse the chain the same way verify does: sig_len then chain_len.
    sl = int.from_bytes(payload[6:8], "big")
    after_sig = payload[8 + sl :]
    cl = int.from_bytes(after_sig[0:2], "big")
    certs = tuple(split_der_certs(after_sig[2 : 2 + cl]))
    assert subca_of_chain(certs) == sub_der
