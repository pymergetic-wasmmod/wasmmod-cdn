"""Thin metal-cdn HTTP client (``pymergetic-metal-cdn-client``)."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from pymergetic.metal.cdn_client.client import ArtifactDownload, CdnClient, ClientError
from pymergetic.metal.cdn_client.errors import (
    die,
    die_client,
    format_client_error,
    format_error,
    hints_for_client_error,
    invoke,
    report,
    report_client_error,
)
from pymergetic.metal.cdn_client.format import hexdump, human_size
from pymergetic.metal.cdn_client.config import (
    TOKEN_SOURCE_API_KEY,
    TOKEN_SOURCE_OIDC,
    ClientConfig,
    TokenSource,
    clear_token,
    config_dir,
    config_path,
    load_config,
    save_config,
    token_source,
)
from pymergetic.metal.cdn_client.contents import (
    ArtifactContents,
    ArtifactContentsSummary,
    DepInfo,
    EmbeddedFileView,
    ImportInfo,
    PackageContents,
    PackExportInfo,
    PackFileInfo,
    PackSectionInfo,
    SigCertInfo,
    SigSectionInfo,
    SourceFileInfo,
    SourceSectionInfo,
    ensure_zlib_artifacts,
    extract_embedded_bytes,
    extract_embedded_file,
    inspect_artifact,
    inspect_upload,
    merge_contents,
    unwrap_mpzl,
    wrap_mpzl,
)
from pymergetic.metal.cdn_client.verify import (
    RequireSignedMode,
    VerifyResult,
    enforce_signed_policy,
    verify_artifact,
)


def resolve_version() -> str:
    try:
        return version("pymergetic-metal-cdn-client")
    except PackageNotFoundError:
        return "0.0.0+unknown"


__version__ = resolve_version()

__all__ = [
    "TOKEN_SOURCE_API_KEY",
    "TOKEN_SOURCE_OIDC",
    "ArtifactContents",
    "ArtifactContentsSummary",
    "ArtifactDownload",
    "CdnClient",
    "ClientConfig",
    "ClientError",
    "DepInfo",
    "EmbeddedFileView",
    "RequireSignedMode",
    "VerifyResult",
    "die",
    "die_client",
    "enforce_signed_policy",
    "extract_embedded_bytes",
    "extract_embedded_file",
    "format_client_error",
    "format_error",
    "hexdump",
    "hints_for_client_error",
    "human_size",
    "ImportInfo",
    "invoke",
    "report",
    "report_client_error",
    "PackageContents",
    "PackExportInfo",
    "PackFileInfo",
    "PackSectionInfo",
    "SigCertInfo",
    "SigSectionInfo",
    "SourceFileInfo",
    "SourceSectionInfo",
    "TokenSource",
    "__version__",
    "clear_token",
    "config_dir",
    "config_path",
    "ensure_zlib_artifacts",
    "inspect_artifact",
    "inspect_upload",
    "load_config",
    "merge_contents",
    "save_config",
    "token_source",
    "unwrap_mpzl",
    "verify_artifact",
    "wrap_mpzl",
]
