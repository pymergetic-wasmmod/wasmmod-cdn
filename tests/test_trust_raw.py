"""Trust admin API + embedded files/raw."""

from __future__ import annotations

import json
import struct
from datetime import UTC
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from pymergetic.metal.cdn.main import create_app
from pymergetic.metal.cdn.settings import Settings
from pymergetic.metal.cdn_client.contents import extract_embedded_bytes


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


def _source(name: str, version: str, files: list[tuple[str, bytes]]) -> bytes:
    out = bytearray(b"MPSR")
    out += struct.pack("<HH", 1, 0)
    for s in (name, version):
        b = s.encode()
        out += struct.pack("<H", len(b)) + b
    out += struct.pack("<H", 0)
    out += struct.pack("<I", len(files))
    for rel, data in files:
        rel_b = rel.encode()
        out += struct.pack("<H", len(rel_b)) + rel_b
        out += struct.pack("<B", 0)
        out += struct.pack("<I", len(data))
        out += struct.pack("<I", len(data))
        out += data
    return bytes(out)


@pytest.fixture
async def client(tmp_path: Path):
    settings = Settings(
        data_dir=tmp_path / "data",
        storage_root=tmp_path / "packs",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'trust.db'}",
        base_path="/",
        require_auth=True,
        allow_open_registration=True,
        csrf_enabled=False,
        rate_limit_enabled=False,
        require_signed="off",
        debug=False,
    )
    app = create_app(settings)
    transport = ASGITransport(app=app)
    async with (
        AsyncClient(transport=transport, base_url="http://test") as ac,
        app.router.lifespan_context(app),
    ):
        yield ac


async def _admin_token(ac: AsyncClient) -> str:
    reg = await ac.post(
        "/auth/register",
        json={"email": "admin@example.com", "display_name": "A", "password": "secret123"},
    )
    assert reg.status_code == 201, reg.text
    key = await ac.post(
        "/auth/token",
        json={"email": "admin@example.com", "password": "secret123", "name": "t"},
    )
    assert key.status_code in (200, 201), key.text
    return key.json()["key"]


@pytest.mark.asyncio
async def test_trust_crud_and_raw_file(client: AsyncClient, tmp_path: Path) -> None:
    token = await _admin_token(client)
    headers = {"Authorization": f"Bearer {token}"}

    # Minimal self-signed PEM as trust root
    from datetime import datetime, timedelta

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    now = datetime.now(UTC)
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "cdn-test-ca")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    pem = cert.public_bytes(serialization.Encoding.PEM)

    listed = await client.get("/admin/trust", headers=headers)
    assert listed.status_code == 200
    assert listed.json() == []

    added = await client.post(
        "/admin/trust",
        headers=headers,
        files={"file": ("ca.pem", pem, "application/x-pem-file")},
        data={"name": "cdn-test-ca"},
    )
    assert added.status_code == 201, added.text
    root_id = added.json()["id"]
    assert added.json()["name"] == "cdn-test-ca"

    listed2 = await client.get("/admin/trust", headers=headers)
    assert len(listed2.json()) == 1

    # Publish artifact with source file
    src = _source("demo", "1.0", [("hello.py", b"print(1)\n")])
    wasm = b"\x00asm\x01\x00\x00\x00" + _custom_section("wasmmod.source", src)
    meta = {
        "package": "demo",
        "version": "0.1.0",
        "lead": True,
        "pin": True,
        "force": True,
    }
    pub = await client.post(
        "/publish",
        headers=headers,
        data={"meta": json.dumps(meta)},
        files={"files": ("demo.wasm", wasm, "application/wasm")},
    )
    assert pub.status_code == 201, pub.text

    view = await client.get(
        "/artifacts/lead/demo.wasm/files",
        params={"path": "hello.py"},
    )
    assert view.status_code == 200, view.text
    assert view.json()["text"] == "print(1)\n"

    raw = await client.get(
        "/artifacts/lead/demo.wasm/files/raw",
        params={"path": "hello.py"},
    )
    assert raw.status_code == 200
    assert raw.content == b"print(1)\n"
    assert "attachment" in raw.headers.get("content-disposition", "")

    body, section, _kind = extract_embedded_bytes(wasm, "hello.py")
    assert body == b"print(1)\n"
    assert section == "source"

    deleted = await client.delete(f"/admin/trust/{root_id}", headers=headers)
    assert deleted.status_code == 204
    assert (await client.get("/admin/trust", headers=headers)).json() == []
