"""Shared HTTP types + urllib helpers for CdnClient."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class ClientError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class ArtifactDownload:
    """Result of an artifact GET (supports 304 via ``not_modified``)."""

    data: bytes | None
    etag: str | None
    not_modified: bool
    status: int


def _header(headers: Any, name: str) -> str:
    """Read an HTTP header case-insensitively."""
    if headers is None:
        return ""
    get = getattr(headers, "get", None)
    if callable(get):
        for key in (name, name.lower(), name.title()):
            value = get(key)
            if value:
                return str(value)
    try:
        return str(headers[name])
    except (KeyError, TypeError, IndexError):
        return ""


def _encode_multipart(fields: list[tuple[str, Any]]) -> tuple[bytes, str]:
    boundary = "----MetalCdnBoundary7MA4YWxkTrZu0gW"
    lines: list[bytes] = []
    for name, value in fields:
        lines.append(f"--{boundary}\r\n".encode())
        if isinstance(value, tuple):
            filename, content, content_type = value
            lines.append(
                (
                    f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
                    f"Content-Type: {content_type}\r\n\r\n"
                ).encode()
            )
            lines.append(content)
            lines.append(b"\r\n")
        else:
            lines.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
            lines.append(str(value).encode())
            lines.append(b"\r\n")
    lines.append(f"--{boundary}--\r\n".encode())
    return b"".join(lines), f"multipart/form-data; boundary={boundary}"
