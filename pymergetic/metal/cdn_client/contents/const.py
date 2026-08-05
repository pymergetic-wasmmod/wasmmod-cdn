"""Shared magics / section names for wasmmod artifact inventory."""

from __future__ import annotations

MPZL_MAGIC = b"MPZL"
PACK_MAGIC = b"MPWP"
SOURCE_MAGIC = b"MPSR"
IMPORTS_MAGIC = b"MPWI"
DEPS_MAGIC = b"MPWD"
MPWS_MAGIC = b"MPWS"
MPWS_VER = 1
PACK_SECTION = "wasmmod.pack"
SOURCE_SECTION = "wasmmod.source"
IMPORTS_SECTION = "wasmmod.imports"
DEPS_SECTION = "wasmmod.deps"
SIG_SECTION = "wasmmod.sig"

AOT_SECTION_TYPE_CUSTOM = 100
AOT_CUSTOM_SECTION_RAW = 0
AOT_SECTION_NAMES: dict[int, str] = {
    0: "target_info",
    1: "init_data",
    2: "text",
    3: "function",
    4: "export",
    5: "relocation",
    6: "signature",
    AOT_SECTION_TYPE_CUSTOM: "custom",
}

WASM_SECTION_NAMES: dict[int, str] = {
    0: "custom",
    1: "type",
    2: "import",
    3: "function",
    4: "table",
    5: "memory",
    6: "global",
    7: "export",
    8: "start",
    9: "element",
    10: "code",
    11: "data",
    12: "datacount",
}

KIND_NAMES = {1: "py", 2: "mpy", 3: "raw", 4: "pyc"}
SIG_AUTO = 255
SECTION_RAW_LIMIT_CAP = 1 << 20
