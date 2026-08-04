"""Artifact contents inspection for CDN index JSON."""

from __future__ import annotations

import struct
import zlib

import pytest

from pymergetic.metal.cdn_client.contents import (
    ensure_zlib_artifacts,
    extract_container_section,
    extract_embedded_bytes,
    inspect_artifact,
    inspect_upload,
    list_container_sections,
    list_pack_symbols,
    pack_addr2line,
    pack_disasm,
    pack_has_dwarf,
    pack_locations,
    pack_mpy_disasm,
    parse_pack_payload,
    slice_bytes,
    unwrap_mpzl,
    without_sig_section,
    wrap_mpzl,
)


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


def _pack_v3(name: str, files: list[tuple[str, int, bytes]], exports: list[tuple[str, str, str, int]] | None = None) -> bytes:
    name_b = name.encode()
    out = bytearray(b"MPWP")
    out += struct.pack("<HH", 3, 0)
    out += struct.pack("<H", len(name_b)) + name_b
    out += struct.pack("<I", len(files))
    for rel, kind, data in files:
        rel_b = rel.encode()
        out += struct.pack("<H", len(rel_b)) + rel_b
        out += struct.pack("<B", kind)
        out += struct.pack("<B", 0)
        out += struct.pack("<I", len(data))
        out += struct.pack("<I", len(data))
        out += data
    exports = exports or []
    out += struct.pack("<I", len(exports))
    for module, func, export, sig in exports:
        for s in (module, func, export):
            b = s.encode()
            out += struct.pack("<H", len(b)) + b
        out += struct.pack("<B", sig)
    return bytes(out)


def _source(name: str, version: str, files: list[tuple[str, bytes]]) -> bytes:
    out = bytearray(b"MPSR")
    out += struct.pack("<HH", 1, 0)
    for s in (name, version):
        b = s.encode()
        out += struct.pack("<H", len(b)) + b
    out += struct.pack("<H", 0)  # tags
    out += struct.pack("<I", len(files))
    for rel, data in files:
        rel_b = rel.encode()
        out += struct.pack("<H", len(rel_b)) + rel_b
        out += struct.pack("<B", 0)
        out += struct.pack("<I", len(data))
        out += struct.pack("<I", len(data))
        out += data
    return bytes(out)


def test_unwrap_mpzl() -> None:
    raw = b"\x00asm\x01\x00\x00\x00hello"
    blob = b"MPZL" + struct.pack("<I", len(raw)) + zlib.compress(raw, 9)
    assert unwrap_mpzl(blob) == raw
    assert unwrap_mpzl(raw) == raw


def test_inspect_pack_and_source() -> None:
    pack = _pack_v3(
        "hello",
        [("__init__.py", 1, b"x=1\n"), ("util.py", 1, b"y=2\n")],
        exports=[("", "add", "add", 2)],
    )
    src = _source("hello", "0.1.0", [("src/__init__.py", b"x=1\n")])
    wasm = _minimal_wasm(
        _custom_section("wasmmod.pack", pack),
        _custom_section("wasmmod.source", src),
    )
    info = inspect_artifact(wasm, filename="hello.wasm")
    assert info.kind.value == "wasm"
    assert info.signed is False
    assert info.pack is not None
    assert info.pack.name == "hello"
    assert [f.path for f in info.pack.files] == ["__init__.py", "util.py"]
    assert info.pack.exports[0].export == "add"
    assert info.source is not None
    assert info.source.pkg_version == "0.1.0"

    # Container sections inventory (customs + any standard sections present).
    names = [s.name for s in info.sections]
    assert "wasmmod.pack" in names
    assert "wasmmod.source" in names
    assert all(s.role == "meta" for s in info.sections if s.name.startswith("wasmmod."))

    z = b"MPZL" + struct.pack("<I", len(wasm)) + zlib.compress(wasm, 9)
    contents = inspect_upload({"hello.wasm.zlib": z})
    assert contents.schema_version == 1
    assert contents.name == "hello"
    assert contents.pkg_version == "0.1.0"
    assert contents.has_pack is True
    assert contents.has_source is True
    assert "util.py" in contents.pack_files
    assert "add" in contents.exports


def test_ensure_zlib_adds_twin() -> None:
    raw = b"\0asm" + b"\x01\0\0\0"
    out = ensure_zlib_artifacts({"hello.wasm": raw})
    assert set(out) == {"hello.wasm", "hello.wasm.zlib"}
    assert unwrap_mpzl(out["hello.wasm.zlib"]) == raw
    assert out["hello.wasm"] == raw

    zonly = ensure_zlib_artifacts({"hello.wasm.zlib": wrap_mpzl(raw)})
    assert list(zonly) == ["hello.wasm.zlib"]


def test_inspect_elf_pack_section() -> None:
    """ELF64 ET_REL with .wasmmod.pack is kind=elf and readable."""
    import subprocess
    import sys
    from pathlib import Path

    wasmmod_tools = Path("/home/ladmin/Devel/os-sdk/packages/metalpython/extmod/wasmmod/tools")
    if not (wasmmod_tools / "wasmmod_elf.py").is_file():
        pytest.skip("wasmmod_elf.py not found")

    c = Path("/tmp/cdn_elf_hello.c")
    c.write_text("int hello(void){return 42;}\n")
    o = Path("/tmp/cdn_elf_hello.o")
    subprocess.check_call(
        ["gcc", "-c", "-ffreestanding", "-fno-pic", "-O2", "-o", str(o), str(c)]
    )
    sys.path.insert(0, str(wasmmod_tools))
    from wasmmod_elf import append_section  # type: ignore

    pack = _pack_v3("hello", [], exports=[("", "hello", "hello", 0)])
    elf = append_section(o.read_bytes(), "wasmmod.pack", pack)
    info = inspect_artifact(elf, filename="hello.elf")
    assert info.kind.value == "elf"
    assert info.pack is not None
    assert info.pack.name == "hello"
    assert info.pack.exports[0].export == "hello"

    out = ensure_zlib_artifacts({"hello.elf": elf})
    assert "hello.elf.zlib" in out
    assert unwrap_mpzl(out["hello.elf.zlib"]) == elf


def test_without_sig_section_elf_wpse() -> None:
    """ELF WPSE strip matches wasmmod (naked cookie drop + signed restore)."""
    import subprocess
    import sys
    from pathlib import Path

    wasmmod_tools = Path("/home/ladmin/Devel/os-sdk/packages/metalpython/extmod/wasmmod/tools")
    if not (wasmmod_tools / "wasmmod_elf.py").is_file():
        pytest.skip("wasmmod_elf.py not found")

    c = Path("/tmp/cdn_elf_wpse.c")
    c.write_text("int hello(void){return 42;}\n")
    o = Path("/tmp/cdn_elf_wpse.o")
    subprocess.check_call(
        ["gcc", "-c", "-ffreestanding", "-fPIC", "-O2", "-o", str(o), str(c)]
    )
    sys.path.insert(0, str(wasmmod_tools))
    from wasmmod_elf import append_section  # type: ignore

    base = o.read_bytes()
    with_pack = append_section(base, "wasmmod.pack", b"pack")
    # Unsigned + WPSE: drop cookie only (signed-body digest is clean object+pack).
    naked = without_sig_section(with_pack)
    assert naked == with_pack[: len(with_pack) - 28]
    assert naked[:4] == b"\x7fELF"

    with_sig = append_section(with_pack, "wasmmod.sig", b"\x00" * 16)
    stripped = without_sig_section(with_sig)
    assert stripped == naked
    from wasmmod_elf import find_section  # type: ignore

    assert find_section(stripped, "wasmmod.sig") is None
    assert find_section(stripped, "wasmmod.pack") is not None


@pytest.mark.asyncio
async def test_publish_stores_contents(tmp_path) -> None:
    from httpx import ASGITransport, AsyncClient

    from pymergetic.metal.cdn.main import create_app
    from pymergetic.metal.cdn.settings import Settings

    settings = Settings(
        data_dir=tmp_path / "data",
        storage_root=tmp_path / "packs",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'c.db'}",
        base_path="/cdn",
        require_auth=True,
        allow_open_registration=True,
        session_secret="test-secret",
        csrf_enabled=False,
        rate_limit_enabled=False,
        debug=False,
    )
    app = create_app(settings)
    pack = _pack_v3("demo", [("__init__.py", 1, b"pass\n")])
    wasm = _minimal_wasm(_custom_section("wasmmod.pack", pack))
    transport = ASGITransport(app=app)
    async with (
        AsyncClient(transport=transport, base_url="http://test") as ac,
        app.router.lifespan_context(app),
    ):
        await ac.post(
            "/cdn/auth/register",
            json={"email": "c@example.com", "password": "secret123", "display_name": "C"},
        )
        tok = (
            await ac.post(
                "/cdn/auth/token",
                json={"email": "c@example.com", "password": "secret123", "name": "t"},
            )
        ).json()["key"]
        await ac.post(
            "/cdn/packages/demo/claim",
            headers={"Authorization": f"Bearer {tok}"},
        )
        import json

        pub = await ac.post(
            "/cdn/publish",
            data={
                "meta": json.dumps(
                    {"package": "demo", "version": "1.0.0", "lead": True, "pin": True, "deps": {}}
                )
            },
            files=[("files", ("demo.wasm", wasm, "application/octet-stream"))],
            headers={"Authorization": f"Bearer {tok}"},
        )
        assert pub.status_code == 201, pub.text
        body = pub.json()
        assert body["contents"]["has_pack"] is True
        assert "__init__.py" in body["contents"]["pack_files"]

        entry = (await ac.get("/cdn/packages/demo")).json()
        assert entry["contents"]["name"] == "demo"
        assert entry["contents"]["pack_files"] == ["__init__.py"]

        insp = await ac.get("/cdn/artifacts/lead/demo.wasm/inspect")
        assert insp.status_code == 200, insp.text
        assert insp.json()["pack"]["name"] == "demo"

        file_view = await ac.get("/cdn/artifacts/lead/demo.wasm/files", params={"path": "__init__.py"})
        assert file_view.status_code == 200, file_view.text
        assert file_view.json()["text"] == "pass\n"


def test_parse_pack_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        parse_pack_payload(b"nope")


def test_extract_embedded_source_and_pack() -> None:
    from pymergetic.metal.cdn_client.contents import extract_embedded_file

    pack = _pack_v3("hello", [("__init__.py", 1, b"x = 1\n")])
    src = _source("hello", "0.1.0", [("src/__init__.py", b"print('hi')\n")])
    wasm = _minimal_wasm(
        _custom_section("wasmmod.pack", pack),
        _custom_section("wasmmod.source", src),
    )
    view = extract_embedded_file(wasm, "src/__init__.py")
    assert view.section == "source"
    assert view.text == "print('hi')\n"
    assert view.binary is False

    pack_view = extract_embedded_file(wasm, "__init__.py")
    assert pack_view.section == "pack"
    assert pack_view.kind == "py"
    assert pack_view.text == "x = 1\n"


def _mpws(sig: bytes, chain: bytes = b"") -> bytes:
    out = bytearray(b"MPWS")
    out.append(1)
    out.append(0)
    out += len(sig).to_bytes(2, "big")
    out += sig
    out += len(chain).to_bytes(2, "big")
    out += chain
    return bytes(out)


def test_inspect_mpws_signature() -> None:
    # Minimal DER SEQUENCE { INTEGER 0 } as stand-in leaf cert
    leaf = bytes([0x30, 0x03, 0x02, 0x01, 0x00])
    sig = b"\x30" + bytes(range(70))  # fake ECDSA blob
    payload = _mpws(sig, leaf)
    wasm = _minimal_wasm(_custom_section("wasmmod.sig", payload))
    art = inspect_artifact(wasm, filename="hello.wasm")
    assert art.signed is True
    assert art.sig is not None
    assert art.sig.format == "mpws"
    assert art.sig.version == 1
    assert art.sig.sig_len == len(sig)
    assert art.sig.chain_len == len(leaf)
    assert art.sig.signed_len == 8  # header only after strip
    assert len(art.sig.certs) == 1
    assert art.sig.certs[0].role == "leaf"
    assert len(art.sig.sig_sha256) == 64


def test_parse_deps_and_publish_fill() -> None:
    from pymergetic.metal.cdn_client.contents import (
        DEPS_SECTION,
        inspect_artifact,
        parse_deps_payload,
    )

    payload = bytearray(b"MPWD")
    payload += struct.pack("<H", 1)
    payload += struct.pack("<I", 1)
    for s in ("hello", "0.1.0"):
        b = s.encode()
        payload += struct.pack("<H", len(b)) + b
    deps = parse_deps_payload(bytes(payload))
    assert deps[0].name == "hello" and deps[0].version == "0.1.0"

    wasm = _minimal_wasm(_custom_section(DEPS_SECTION, bytes(payload)))
    art = inspect_artifact(wasm, filename="client.wasm")
    assert art.deps[0].name == "hello"
    contents = inspect_upload({"client.wasm": wasm})
    assert contents.deps == {"hello": "0.1.0"}
    assert contents.has_deps is True


def test_export_typesig_from_wasm() -> None:
    """Pack binder tag 255 is SIG_AUTO; inspect fills real Wasm types."""
    from pymergetic.metal.cdn_client.contents import describe_binder_sig

    assert describe_binder_sig(255) == "auto"
    assert describe_binder_sig(2) == "(i32, i32) -> i32"

    # type: (i32,i32)->i32 ; function[0]=type0 ; export "add" -> func 0
    type_sec = bytes(
        [
            1,  # 1 type
            0x60,
            2,
            0x7F,
            0x7F,  # 2 params i32 i32
            1,
            0x7F,  # 1 result i32
        ]
    )
    func_sec = bytes([1, 0])  # 1 func, type idx 0
    # export "add" func 0
    name = b"add"
    export_sec = bytes([1, len(name)]) + name + bytes([0, 0])
    wasm = _minimal_wasm(
        bytes([1]) + _uleb(len(type_sec)) + type_sec,
        bytes([3]) + _uleb(len(func_sec)) + func_sec,
        # empty code section required? not for our parser
        bytes([7]) + _uleb(len(export_sec)) + export_sec,
        _custom_section(
            "wasmmod.pack",
            _pack_v3("hello", [], exports=[("", "add", "add", 255)]),
        ),
    )
    art = inspect_artifact(wasm, filename="hello.wasm")
    assert art.pack is not None
    assert art.pack.exports[0].sig == 255
    assert art.pack.exports[0].typesig == "(i32, i32) -> i32"


def _wasm_code_section(body: bytes) -> bytes:
    return bytes([10]) + _uleb(len(body)) + body


def test_list_container_sections_wasm_code() -> None:
    code_body = b"\x01\x04\x00\x41\x2a\x0b"  # 1 func: i32.const 42; end
    wasm = _minimal_wasm(
        _custom_section("wasmmod.pack", _pack_v3("hello", [])),
        _wasm_code_section(code_body),
    )
    secs = list_container_sections(wasm)
    by_name = {s.name: s for s in secs}
    assert "code" in by_name
    assert by_name["code"].role == "code"
    assert by_name["code"].type_id == 10
    assert by_name["code"].size == len(code_body)
    payload = extract_container_section(wasm, index=by_name["code"].index)
    assert payload == code_body

    info = inspect_artifact(wasm, filename="hello.wasm")
    assert any(s.name == "code" and s.role == "code" for s in info.sections)

    # MPZL unwrap path
    z = wrap_mpzl(wasm)
    assert list_container_sections(z) == secs
    assert extract_container_section(z, index=by_name["code"].index) == code_body


def test_list_container_sections_elf_text() -> None:
    import subprocess
    import sys
    from pathlib import Path

    wasmmod_tools = Path("/home/ladmin/Devel/os-sdk/packages/metalpython/extmod/wasmmod/tools")
    if not (wasmmod_tools / "wasmmod_elf.py").is_file():
        pytest.skip("wasmmod_elf.py not found")

    c = Path("/tmp/cdn_elf_text.c")
    c.write_text("int hello(void){return 42;}\n")
    o = Path("/tmp/cdn_elf_text.o")
    subprocess.check_call(
        ["gcc", "-c", "-ffreestanding", "-fPIC", "-O2", "-o", str(o), str(c)]
    )
    sys.path.insert(0, str(wasmmod_tools))
    from wasmmod_elf import append_section  # type: ignore

    pack = _pack_v3("hello", [], exports=[("", "hello", "hello", 0)])
    elf = append_section(o.read_bytes(), "wasmmod.pack", pack)
    secs = list_container_sections(elf)
    names = [s.name for s in secs]
    assert ".text" in names
    text = next(s for s in secs if s.name == ".text")
    assert text.role == "code"
    assert text.size > 0
    payload = extract_container_section(elf, index=text.index)
    assert len(payload) == text.size

    meta = next(s for s in secs if s.name in (".wasmmod.pack", "wasmmod.pack") or s.name.endswith("wasmmod.pack"))
    assert meta.role == "meta"

    info = inspect_artifact(elf, filename="hello.elf")
    assert any(s.name == ".text" and s.role == "code" for s in info.sections)


def _hello_elf_bytes() -> bytes:
    from pathlib import Path

    candidates = [
        Path(__file__).resolve().parents[2]
        / "metalpython"
        / "extmod"
        / "wasmmod"
        / "examples"
        / "packs"
        / "hello.elf",
        Path("/home/ladmin/Devel/os-sdk/packages/metalpython/extmod/wasmmod/examples/packs/hello.elf"),
    ]
    for p in candidates:
        if p.is_file():
            return p.read_bytes()
    pytest.skip("hello.elf fixture not found")


def test_slice_bytes_limit_cap() -> None:
    body = bytes(range(256)) * 8
    assert slice_bytes(body, offset=10, limit=5) == body[10:15]
    assert slice_bytes(body, offset=0, limit=None) == body
    huge = b"x" * ((1 << 20) + 50)
    assert len(slice_bytes(huge, offset=0, limit=(1 << 20) + 50)) == (1 << 20)
    with pytest.raises(ValueError):
        slice_bytes(body, offset=-1)


def test_pack_symbols_addr2line_disasm_hello_elf() -> None:
    data = _hello_elf_bytes()
    syms = list_pack_symbols(data)
    assert any(s.name == "hello" for s in syms)
    assert pack_has_dwarf(data) is True

    hello = next(s for s in syms if s.name == "hello")
    locs = pack_addr2line(data, hello.offset)
    assert locs
    assert any(loc.path for loc in locs)

    named = pack_locations(data, "hello")
    assert named
    assert any(loc.role in ("sym", "dwarf", "def", "twin") for loc in named)

    assert hello.section_index is not None
    secs = list_container_sections(data)
    text = next(s for s in secs if s.name == ".text")
    assert hello.section_index == text.index  # shndx parity (not compacted)
    lines = pack_disasm(data, hello.section_index, offset=hello.offset, limit=8)
    assert lines
    assert all(ln.text for ln in lines)
    raw = extract_container_section(data, index=hello.section_index)
    assert len(raw) == text.size


def test_pack_mpy_disasm_hello_elf() -> None:
    data = _hello_elf_bytes()
    body, section, _kind = extract_embedded_bytes(
        data, "__init__.upy.mpy6.sib31.mpy"
    )
    assert section in ("pack", "source")
    lines = pack_mpy_disasm(body, limit=16)
    assert lines
    assert lines[0].text.startswith("mpy_hdr") or lines[0].raw_hex


@pytest.mark.asyncio
async def test_symbols_and_addr2line_api(tmp_path) -> None:
    from httpx import ASGITransport, AsyncClient

    from pymergetic.metal.cdn.main import create_app
    from pymergetic.metal.cdn.settings import Settings

    data = _hello_elf_bytes()
    settings = Settings(
        data_dir=tmp_path / "data",
        storage_root=tmp_path / "packs",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'c.db'}",
        base_path="/cdn",
        require_auth=True,
        allow_open_registration=True,
        session_secret="test-secret",
        csrf_enabled=False,
        rate_limit_enabled=False,
        debug=False,
        require_signed="off",
    )
    app = create_app(settings)
    transport = ASGITransport(app=app)
    async with (
        AsyncClient(transport=transport, base_url="http://test") as ac,
        app.router.lifespan_context(app),
    ):
        await ac.post(
            "/cdn/auth/register",
            json={"email": "elf@example.com", "password": "secret123", "display_name": "E"},
        )
        tok = (
            await ac.post(
                "/cdn/auth/token",
                json={"email": "elf@example.com", "password": "secret123", "name": "t"},
            )
        ).json()["key"]
        await ac.post(
            "/cdn/packages/hello/claim",
            headers={"Authorization": f"Bearer {tok}"},
        )
        import json

        pub = await ac.post(
            "/cdn/publish",
            data={
                "meta": json.dumps(
                    {
                        "package": "hello",
                        "version": "0.1.0",
                        "lead": True,
                        "pin": True,
                        "deps": {},
                    }
                )
            },
            files=[("files", ("hello.elf", data, "application/octet-stream"))],
            headers={"Authorization": f"Bearer {tok}"},
        )
        assert pub.status_code == 201, pub.text

        syms = await ac.get("/cdn/artifacts/lead/hello.elf/symbols")
        assert syms.status_code == 200, syms.text
        names = [s["name"] for s in syms.json()]
        assert "hello" in names

        hello = next(s for s in syms.json() if s["name"] == "hello")
        a2l = await ac.get(
            "/cdn/artifacts/lead/hello.elf/addr2line",
            params={"addr": hello["offset"]},
        )
        assert a2l.status_code == 200, a2l.text
        assert isinstance(a2l.json(), list)

        locs = await ac.get(
            "/cdn/artifacts/lead/hello.elf/locations",
            params={"name": "hello"},
        )
        assert locs.status_code == 200, locs.text
        assert locs.json()

        dasm = await ac.get(
            "/cdn/artifacts/lead/hello.elf/disasm",
            params={"index": hello["section_index"], "offset": hello["offset"], "limit": 8},
        )
        assert dasm.status_code == 200, dasm.text
        assert dasm.json()

        raw = await ac.get(
            "/cdn/artifacts/lead/hello.elf/sections/raw",
            params={"index": hello["section_index"], "offset": 0, "limit": 16},
        )
        assert raw.status_code == 200, raw.text
        assert len(raw.content) <= 16

        mpy = await ac.get(
            "/cdn/artifacts/lead/hello.elf/files/mpy-disasm",
            params={"path": "__init__.upy.mpy6.sib31.mpy", "limit": 12},
        )
        assert mpy.status_code == 200, mpy.text
        assert mpy.json()

