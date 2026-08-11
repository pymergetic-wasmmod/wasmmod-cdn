from pymergetic.wasmmod.cdn.layout import ChannelLayout


def test_classify_artifacts() -> None:
    kind, arch, ver, enc = ChannelLayout.classify_artifact("hello.wasm.zlib")
    assert (kind, arch, ver, enc) == ("wasm", None, None, "mpzl")

    kind, arch, ver, enc = ChannelLayout.classify_artifact("hello.x86_64.aot6")
    assert kind == "aot"
    assert arch == "x86_64"
    assert ver == 6
    assert enc == "raw"

    kind, arch, ver, enc = ChannelLayout.classify_artifact("hello.elf")
    assert (kind, arch, ver, enc) == ("elf", None, None, "raw")

    kind, arch, ver, enc = ChannelLayout.classify_artifact("hello.x86_64.elf.zlib")
    assert (kind, arch, ver, enc) == ("elf", "x86_64", None, "mpzl")

    kind, arch, ver, enc = ChannelLayout.classify_artifact("metal.x86_64.efi")
    assert (kind, arch, ver, enc) == ("efi", "x86_64", None, "raw")

    kind, arch, ver, enc = ChannelLayout.classify_artifact(
        "pymergetic.metal.arch.x86_64.efi"
    )
    assert (kind, arch, ver, enc) == ("efi", "x86_64", None, "raw")
    kind, arch, ver, enc = ChannelLayout.classify_artifact(
        "pymergetic.metal.arch.x86_64.elf"
    )
    assert (kind, arch, ver, enc) == ("elf", "x86_64", None, "raw")
    kind, arch, ver, enc = ChannelLayout.classify_artifact(
        "pymergetic.metal.arch.x86_64.efi.zlib"
    )
    assert (kind, arch, ver, enc) == ("efi", "x86_64", None, "mpzl")
    ChannelLayout.validate_package_name("pymergetic.metal.arch.x86_64")

    kind, arch, ver, enc = ChannelLayout.classify_artifact(
        "pymergetic.metal.arch.wasm.wasm"
    )
    assert (kind, arch, ver, enc) == ("wasm", None, None, "raw")
    kind, arch, ver, enc = ChannelLayout.classify_artifact(
        "pymergetic.metal.arch.wasm.mjs"
    )
    assert (kind, arch, ver, enc) == ("mjs", None, None, "raw")
    ChannelLayout.validate_package_name("pymergetic.metal.arch.wasm")
    ChannelLayout.validate_package_name("pymergetic.metal.arch.x86")
    kind, arch, ver, enc = ChannelLayout.classify_artifact(
        "pymergetic.metal.arch.x86.elf"
    )
    assert (kind, arch, ver, enc) == ("elf", "x86", None, "raw")
    kind, arch, ver, enc = ChannelLayout.classify_artifact(
        "pymergetic.metal.arch.x86.efi"
    )
    assert (kind, arch, ver, enc) == ("efi", "x86", None, "raw")


def test_channel_keys() -> None:
    lead = ChannelLayout.lead()
    assert lead.index_key() == "index.json"
    assert lead.artifact_key("hello.wasm") == "hello.wasm"

    pin = ChannelLayout.pin("0.1.0")
    assert pin.name == "@0.1.0"
    assert pin.index_key() == "@0.1.0/index.json"
    assert pin.artifact_key("hello.wasm") == "@0.1.0/hello.wasm"
