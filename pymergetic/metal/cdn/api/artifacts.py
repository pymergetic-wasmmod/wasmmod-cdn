"""Artifact GET/HEAD + inspect/files/disasm endpoints (with federation forward)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from pymergetic.metal.cdn.api.artifact_io import (
    _artifact_or_forward,
    _embedded_raw_response,
    _load_artifact_bytes_fed,
    _section_raw_response,
)
from pymergetic.metal.cdn.api.deps import SettingsDep, StorageDep
from pymergetic.metal.cdn.api.fed_deps import FederationProxyDep, FederationRegistryDep
from pymergetic.metal.cdn_client.contents import (
    ArtifactContents,
    ContainerSectionInfo,
    DisasmLineInfo,
    EmbeddedFileView,
    LocationInfo,
    SymbolInfo,
    extract_container_section,
    extract_embedded_bytes,
    extract_embedded_file,
    inspect_artifact,
    list_container_sections,
    list_pack_symbols,
    pack_addr2line,
    pack_disasm,
    pack_locations,
    pack_mpy_disasm,
    slice_bytes,
)

artifacts_router = APIRouter(prefix="/artifacts", tags=["artifacts"])


@artifacts_router.api_route("/lead/{filename}", methods=["GET", "HEAD"])
async def get_artifact_lead(
    filename: str,
    request: Request,
    storage: StorageDep,
    settings: SettingsDep,
    reg: FederationRegistryDep,
    proxy: FederationProxyDep,
) -> Response:
    return await _artifact_or_forward(
        storage=storage,
        request=request,
        settings=settings,
        reg=reg,
        proxy=proxy,
        channel="lead",
        filename=filename,
        peer_path=f"/artifacts/lead/{filename}",
    )


@artifacts_router.api_route("/pin/{version}/{filename}", methods=["GET", "HEAD"])
async def get_artifact_pinned(
    version: str,
    filename: str,
    request: Request,
    storage: StorageDep,
    settings: SettingsDep,
    reg: FederationRegistryDep,
    proxy: FederationProxyDep,
) -> Response:
    return await _artifact_or_forward(
        storage=storage,
        request=request,
        settings=settings,
        reg=reg,
        proxy=proxy,
        channel=f"@{version}",
        filename=filename,
        peer_path=f"/artifacts/pin/{version}/{filename}",
    )


@artifacts_router.get("/lead/{filename}/inspect", response_model=ArtifactContents)
async def inspect_artifact_lead(
    filename: str,
    request: Request,
    storage: StorageDep,
    reg: FederationRegistryDep,
    proxy: FederationProxyDep,
) -> ArtifactContents:
    data = await _load_artifact_bytes_fed(
        storage=storage,
        request=request,
        reg=reg,
        proxy=proxy,
        channel="lead",
        filename=filename,
        peer_path=f"/artifacts/lead/{filename}",
    )
    return inspect_artifact(data, filename=filename)


@artifacts_router.get("/pin/{version}/{filename}/inspect", response_model=ArtifactContents)
async def inspect_artifact_pin(
    version: str,
    filename: str,
    request: Request,
    storage: StorageDep,
    reg: FederationRegistryDep,
    proxy: FederationProxyDep,
) -> ArtifactContents:
    data = await _load_artifact_bytes_fed(
        storage=storage,
        request=request,
        reg=reg,
        proxy=proxy,
        channel=f"@{version}",
        filename=filename,
        peer_path=f"/artifacts/pin/{version}/{filename}",
    )
    return inspect_artifact(data, filename=filename)


@artifacts_router.get("/lead/{filename}/files", response_model=EmbeddedFileView)
async def embedded_file_lead(filename: str, path: str, request: Request,
    storage: StorageDep,
    reg: FederationRegistryDep,
    proxy: FederationProxyDep) -> EmbeddedFileView:
    data = await _load_artifact_bytes_fed(
        storage=storage,
        request=request,
        reg=reg,
        proxy=proxy,
        channel="lead",
        filename=filename,
        peer_path=f"/artifacts/lead/{filename}",
    )
    try:
        return extract_embedded_file(data, path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"embedded path not found: {path}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@artifacts_router.get("/pin/{version}/{filename}/files", response_model=EmbeddedFileView)
async def embedded_file_pin(
    version: str, filename: str, path: str, request: Request,
    storage: StorageDep,
    reg: FederationRegistryDep,
    proxy: FederationProxyDep
) -> EmbeddedFileView:
    data = await _load_artifact_bytes_fed(
        storage=storage,
        request=request,
        reg=reg,
        proxy=proxy,
        channel=f"@{version}",
        filename=filename,
        peer_path=f"/artifacts/pin/{version}/{filename}",
    )
    try:
        return extract_embedded_file(data, path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"embedded path not found: {path}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@artifacts_router.get("/lead/{filename}/files/raw")
async def embedded_file_raw_lead(filename: str, path: str, request: Request,
    storage: StorageDep,
    reg: FederationRegistryDep,
    proxy: FederationProxyDep) -> Response:
    data = await _load_artifact_bytes_fed(
        storage=storage,
        request=request,
        reg=reg,
        proxy=proxy,
        channel="lead",
        filename=filename,
        peer_path=f"/artifacts/lead/{filename}",
    )
    try:
        body, _section, _kind, _resolved = extract_embedded_bytes(data, path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"embedded path not found: {path}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _embedded_raw_response(body, path=path)


@artifacts_router.get("/pin/{version}/{filename}/files/raw")
async def embedded_file_raw_pin(
    version: str, filename: str, path: str, request: Request,
    storage: StorageDep,
    reg: FederationRegistryDep,
    proxy: FederationProxyDep
) -> Response:
    data = await _load_artifact_bytes_fed(
        storage=storage,
        request=request,
        reg=reg,
        proxy=proxy,
        channel=f"@{version}",
        filename=filename,
        peer_path=f"/artifacts/pin/{version}/{filename}",
    )
    try:
        body, _section, _kind, _resolved = extract_embedded_bytes(data, path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"embedded path not found: {path}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _embedded_raw_response(body, path=path)


@artifacts_router.get("/lead/{filename}/symbols", response_model=list[SymbolInfo])
async def symbols_lead(filename: str, request: Request,
    storage: StorageDep,
    reg: FederationRegistryDep,
    proxy: FederationProxyDep) -> list[SymbolInfo]:
    data = await _load_artifact_bytes_fed(
        storage=storage,
        request=request,
        reg=reg,
        proxy=proxy,
        channel="lead",
        filename=filename,
        peer_path=f"/artifacts/lead/{filename}",
    )
    return list_pack_symbols(data)


@artifacts_router.get("/pin/{version}/{filename}/symbols", response_model=list[SymbolInfo])
async def symbols_pin(version: str, filename: str, request: Request,
    storage: StorageDep,
    reg: FederationRegistryDep,
    proxy: FederationProxyDep) -> list[SymbolInfo]:
    data = await _load_artifact_bytes_fed(
        storage=storage,
        request=request,
        reg=reg,
        proxy=proxy,
        channel=f"@{version}",
        filename=filename,
        peer_path=f"/artifacts/pin/{version}/{filename}",
    )
    return list_pack_symbols(data)


@artifacts_router.get("/lead/{filename}/addr2line", response_model=list[LocationInfo])
async def addr2line_lead(filename: str, addr: int, request: Request,
    storage: StorageDep,
    reg: FederationRegistryDep,
    proxy: FederationProxyDep) -> list[LocationInfo]:
    """Map ``addr`` (decimal integer query) to source/symbol locations.

    Pass a decimal integer (e.g. ``?addr=16``). Hex ``0x`` prefixes are not
    required — clients should convert before calling.
    """
    data = await _load_artifact_bytes_fed(
        storage=storage,
        request=request,
        reg=reg,
        proxy=proxy,
        channel="lead",
        filename=filename,
        peer_path=f"/artifacts/lead/{filename}",
    )
    return pack_addr2line(data, addr)


@artifacts_router.get("/pin/{version}/{filename}/addr2line", response_model=list[LocationInfo])
async def addr2line_pin(
    version: str, filename: str, addr: int, request: Request,
    storage: StorageDep,
    reg: FederationRegistryDep,
    proxy: FederationProxyDep
) -> list[LocationInfo]:
    """Pin variant of addr2line (``addr`` is a decimal integer query param)."""
    data = await _load_artifact_bytes_fed(
        storage=storage,
        request=request,
        reg=reg,
        proxy=proxy,
        channel=f"@{version}",
        filename=filename,
        peer_path=f"/artifacts/pin/{version}/{filename}",
    )
    return pack_addr2line(data, addr)


@artifacts_router.get("/lead/{filename}/locations", response_model=list[LocationInfo])
async def locations_lead(filename: str, name: str, request: Request,
    storage: StorageDep,
    reg: FederationRegistryDep,
    proxy: FederationProxyDep) -> list[LocationInfo]:
    data = await _load_artifact_bytes_fed(
        storage=storage,
        request=request,
        reg=reg,
        proxy=proxy,
        channel="lead",
        filename=filename,
        peer_path=f"/artifacts/lead/{filename}",
    )
    return pack_locations(data, name)


@artifacts_router.get("/pin/{version}/{filename}/locations", response_model=list[LocationInfo])
async def locations_pin(
    version: str, filename: str, name: str, request: Request,
    storage: StorageDep,
    reg: FederationRegistryDep,
    proxy: FederationProxyDep
) -> list[LocationInfo]:
    data = await _load_artifact_bytes_fed(
        storage=storage,
        request=request,
        reg=reg,
        proxy=proxy,
        channel=f"@{version}",
        filename=filename,
        peer_path=f"/artifacts/pin/{version}/{filename}",
    )
    return pack_locations(data, name)


@artifacts_router.get("/lead/{filename}/disasm", response_model=list[DisasmLineInfo])
async def disasm_lead(
    filename: str,
    index: int,
    request: Request,
    storage: StorageDep,
    reg: FederationRegistryDep,
    proxy: FederationProxyDep,
    offset: int = 0,
    limit: int = 64,
) -> list[DisasmLineInfo]:
    data = await _load_artifact_bytes_fed(
        storage=storage,
        request=request,
        reg=reg,
        proxy=proxy,
        channel="lead",
        filename=filename,
        peer_path=f"/artifacts/lead/{filename}",
    )
    return pack_disasm(data, index, offset=offset, limit=limit)


@artifacts_router.get("/pin/{version}/{filename}/disasm", response_model=list[DisasmLineInfo])
async def disasm_pin(
    version: str,
    filename: str,
    index: int,
    request: Request,
    storage: StorageDep,
    reg: FederationRegistryDep,
    proxy: FederationProxyDep,
    offset: int = 0,
    limit: int = 64,
) -> list[DisasmLineInfo]:
    data = await _load_artifact_bytes_fed(
        storage=storage,
        request=request,
        reg=reg,
        proxy=proxy,
        channel=f"@{version}",
        filename=filename,
        peer_path=f"/artifacts/pin/{version}/{filename}",
    )
    return pack_disasm(data, index, offset=offset, limit=limit)


@artifacts_router.get("/lead/{filename}/files/mpy-disasm", response_model=list[DisasmLineInfo])
async def mpy_disasm_lead(
    filename: str, path: str, request: Request,
    storage: StorageDep,
    reg: FederationRegistryDep,
    proxy: FederationProxyDep, limit: int = 80
) -> list[DisasmLineInfo]:
    data = await _load_artifact_bytes_fed(
        storage=storage,
        request=request,
        reg=reg,
        proxy=proxy,
        channel="lead",
        filename=filename,
        peer_path=f"/artifacts/lead/{filename}",
    )
    try:
        body, _section, _kind, _resolved = extract_embedded_bytes(data, path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"embedded path not found: {path}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return pack_mpy_disasm(body, limit=limit)


@artifacts_router.get(
    "/pin/{version}/{filename}/files/mpy-disasm", response_model=list[DisasmLineInfo]
)
async def mpy_disasm_pin(
    version: str, filename: str, path: str, request: Request,
    storage: StorageDep,
    reg: FederationRegistryDep,
    proxy: FederationProxyDep, limit: int = 80
) -> list[DisasmLineInfo]:
    data = await _load_artifact_bytes_fed(
        storage=storage,
        request=request,
        reg=reg,
        proxy=proxy,
        channel=f"@{version}",
        filename=filename,
        peer_path=f"/artifacts/pin/{version}/{filename}",
    )
    try:
        body, _section, _kind, _resolved = extract_embedded_bytes(data, path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"embedded path not found: {path}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return pack_mpy_disasm(body, limit=limit)


@artifacts_router.get("/lead/{filename}/sections", response_model=list[ContainerSectionInfo])
async def sections_lead(filename: str, request: Request,
    storage: StorageDep,
    reg: FederationRegistryDep,
    proxy: FederationProxyDep) -> list[ContainerSectionInfo]:
    data = await _load_artifact_bytes_fed(
        storage=storage,
        request=request,
        reg=reg,
        proxy=proxy,
        channel="lead",
        filename=filename,
        peer_path=f"/artifacts/lead/{filename}",
    )
    return list_container_sections(data)


@artifacts_router.get(
    "/pin/{version}/{filename}/sections", response_model=list[ContainerSectionInfo]
)
async def sections_pin(
    version: str, filename: str, request: Request,
    storage: StorageDep,
    reg: FederationRegistryDep,
    proxy: FederationProxyDep
) -> list[ContainerSectionInfo]:
    data = await _load_artifact_bytes_fed(
        storage=storage,
        request=request,
        reg=reg,
        proxy=proxy,
        channel=f"@{version}",
        filename=filename,
        peer_path=f"/artifacts/pin/{version}/{filename}",
    )
    return list_container_sections(data)


@artifacts_router.get("/lead/{filename}/sections/raw")
async def section_raw_lead(
    filename: str,
    index: int,
    request: Request,
    storage: StorageDep,
    reg: FederationRegistryDep,
    proxy: FederationProxyDep,
    offset: int = 0,
    limit: int | None = None,
) -> Response:
    data = await _load_artifact_bytes_fed(
        storage=storage,
        request=request,
        reg=reg,
        proxy=proxy,
        channel="lead",
        filename=filename,
        peer_path=f"/artifacts/lead/{filename}",
    )
    try:
        body = extract_container_section(data, index=index)
        sections = list_container_sections(data)
        name = next((s.name for s in sections if s.index == index), f"section_{index}")
        body = slice_bytes(body, offset=offset, limit=limit)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _section_raw_response(body, index=index, name=name, offset=offset)


@artifacts_router.get("/pin/{version}/{filename}/sections/raw")
async def section_raw_pin(
    version: str,
    filename: str,
    index: int,
    request: Request,
    storage: StorageDep,
    reg: FederationRegistryDep,
    proxy: FederationProxyDep,
    offset: int = 0,
    limit: int | None = None,
) -> Response:
    data = await _load_artifact_bytes_fed(
        storage=storage,
        request=request,
        reg=reg,
        proxy=proxy,
        channel=f"@{version}",
        filename=filename,
        peer_path=f"/artifacts/pin/{version}/{filename}",
    )
    try:
        body = extract_container_section(data, index=index)
        sections = list_container_sections(data)
        name = next((s.name for s in sections if s.index == index), f"section_{index}")
        body = slice_bytes(body, offset=offset, limit=limit)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _section_raw_response(body, index=index, name=name, offset=offset)
