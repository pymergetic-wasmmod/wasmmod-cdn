"""Container section walk / extract / Wasm typesig helpers (compat re-exports)."""
from __future__ import annotations

from pymergetic.wasmmod.cdn_client.contents.section_extract import (
    extract_custom_section,
    extract_custom_section_aot,
    extract_custom_section_elf,
    extract_custom_section_wasm,
    has_section,
)
from pymergetic.wasmmod.cdn_client.contents.section_list import (
    extract_container_section,
    list_container_sections,
)
from pymergetic.wasmmod.cdn_client.contents.section_typesigs import (
    describe_binder_sig,
    enrich_pack_export_typesigs,
    slice_bytes,
    wasm_func_export_typesigs,
)

__all__ = [
    "describe_binder_sig",
    "enrich_pack_export_typesigs",
    "extract_container_section",
    "extract_custom_section",
    "extract_custom_section_aot",
    "extract_custom_section_elf",
    "extract_custom_section_wasm",
    "has_section",
    "list_container_sections",
    "slice_bytes",
    "wasm_func_export_typesigs",
]
