from pymergetic.metal.cdn.layout import ChannelLayout


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


def test_channel_keys() -> None:
    lead = ChannelLayout.lead()
    assert lead.index_key() == "index.json"
    assert lead.artifact_key("hello.wasm") == "hello.wasm"

    pin = ChannelLayout.pin("0.1.0")
    assert pin.name == "@0.1.0"
    assert pin.index_key() == "@0.1.0/index.json"
    assert pin.artifact_key("hello.wasm") == "@0.1.0/hello.wasm"
