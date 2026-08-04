"""HTTP client for metal-cdn (stdlib urllib only)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen

from pymergetic.metal.cdn_client.config import load_config


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


class CdnClient:
    """Minimal urllib client — Bearer token only; no OAuth SDK."""

    def __init__(self, base_url: str, *, token: str | None = None, timeout: float = 60.0) -> None:
        self.base_url = base_url.rstrip("/") + "/"
        self.token = token
        self.timeout = timeout

    @classmethod
    def from_config(cls, *, timeout: float = 60.0, require_token: bool = False) -> CdnClient:
        cfg = load_config()
        url = cfg.get("url")
        token = cfg.get("token")
        if not url:
            raise ClientError("CDN URL not set — metal-cdn login --url … or config url")
        if require_token and not token:
            raise ClientError("not logged in — set token (e.g. metal-cdn login --url …)")
        return cls(str(url), token=str(token) if token else None, timeout=timeout)

    def _url(self, path: str, *, query: dict[str, str] | None = None) -> str:
        url = urljoin(self.base_url, path.lstrip("/"))
        if query:
            return f"{url}?{urlencode(query)}"
        return url

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        multipart: list[tuple[str, Any]] | None = None,
        query: dict[str, str] | None = None,
        extra_headers: dict[str, str] | None = None,
        accept: str | None = "application/json",
    ) -> Any:
        headers: dict[str, str] = {}
        if accept:
            headers["Accept"] = accept
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if extra_headers:
            headers.update(extra_headers)
        data: bytes | None = None
        if json_body is not None:
            data = json.dumps(json_body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        elif multipart is not None:
            data, content_type = _encode_multipart(multipart)
            headers["Content-Type"] = content_type

        req = Request(self._url(path, query=query), data=data, headers=headers, method=method)
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                if not raw:
                    return None
                ctype = _header(resp.headers, "Content-Type")
                if "application/json" in ctype:
                    return json.loads(raw.decode("utf-8"))
                if accept and "application/json" in accept:
                    # Some test/ASGI transports omit Content-Type; still decode JSON bodies.
                    try:
                        return json.loads(raw.decode("utf-8"))
                    except json.JSONDecodeError:
                        pass
                return raw
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            detail = body
            try:
                parsed = json.loads(body)
                detail = str(parsed.get("detail", body))
            except json.JSONDecodeError:
                pass
            raise ClientError(detail, status=exc.code) from exc
        except URLError as exc:
            raise ClientError(f"connection failed: {exc.reason}") from exc

    def register(self, email: str, password: str, display_name: str = "") -> dict[str, Any]:
        return self.request(
            "POST",
            "auth/register",
            json_body={
                "email": email,
                "password": password,
                "display_name": display_name,
            },
        )

    def create_api_key_with_password(
        self, email: str, password: str, name: str = "cli"
    ) -> dict[str, Any]:
        return self.request(
            "POST",
            "auth/token",
            json_body={"email": email, "password": password, "name": name},
        )

    def me(self) -> dict[str, Any]:
        return self.request("GET", "auth/me")

    def status(self) -> dict[str, Any]:
        """Public deployment flags (``GET /status``) — experimental banner, version."""
        return self.request("GET", "status")

    def health(self) -> dict[str, Any]:
        return self.request("GET", "health")

    def claim(self, package: str) -> dict[str, Any]:
        return self.request("POST", f"packages/{package}/claim")

    def promote(self, package: str, version: str) -> dict[str, Any]:
        return self.request(
            "POST",
            f"packages/{package}/promote",
            json_body={"version": version},
        )

    def yank(
        self,
        package: str,
        *,
        reason: str = "yanked",
        channel: str = "lead",
    ) -> dict[str, Any]:
        return self.request(
            "POST",
            f"packages/{package}/yank",
            json_body={"reason": reason, "channel": channel},
        )

    def list_packages(self, *, channel: str = "lead") -> list[Any]:
        return self.request("GET", "packages", query={"channel": channel})

    def search(self, q: str, *, channel: str = "lead") -> list[Any]:
        return self.request("GET", "packages/search", query={"q": q, "channel": channel})

    def get_package(self, name: str, *, channel: str = "lead") -> dict[str, Any]:
        return self.request("GET", f"packages/{name}", query={"channel": channel})

    def get_index(self, *, version: str | None = None) -> dict[str, Any]:
        """Fetch lead or pin ``index.json`` (device-facing)."""
        if version is None:
            return self.request("GET", "index/lead")
        return self.request("GET", f"index/pin/{version}")

    def closure(self, name: str, *, version: str | None = None) -> dict[str, Any]:
        query = {"version": version} if version else None
        return self.request("GET", f"packages/{name}/closure", query=query)

    def set_successor(
        self,
        package: str,
        successor: str,
        *,
        channel: str = "lead",
        deprecated: bool = True,
    ) -> dict[str, Any]:
        return self.request(
            "POST",
            f"packages/{package}/successor",
            json_body={
                "successor": successor,
                "channel": channel,
                "deprecated": deprecated,
            },
        )

    def set_visibility(self, package: str, visibility: str) -> dict[str, Any]:
        return self.request(
            "PUT",
            f"packages/{package}/visibility",
            json_body={"visibility": visibility},
        )

    def publish(
        self,
        *,
        package: str,
        version: str,
        files: list[Path],
        lead: bool = True,
        pin: bool = True,
        aot_version: int | None = None,
        deps: dict[str, str] | None = None,
        maintainer_email: str | None = None,
        description: str | None = None,
        homepage: str | None = None,
        license: str | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        meta: dict[str, Any] = {
            "package": package,
            "version": version,
            "lead": lead,
            "pin": pin,
            "deps": deps or {},
            "force": force,
        }
        if aot_version is not None:
            meta["aot_version"] = aot_version
        if maintainer_email:
            meta["maintainer_email"] = maintainer_email
        if description:
            meta["description"] = description
        if homepage:
            meta["homepage"] = homepage
        if license:
            meta["license"] = license
        parts: list[tuple[str, Any]] = [("meta", json.dumps(meta))]
        for path in files:
            parts.append(("files", (path.name, path.read_bytes(), "application/octet-stream")))
        return self.request("POST", "publish", multipart=parts)

    def _artifact_prefix(self, filename: str, *, version: str | None = None) -> str:
        if version is None:
            return f"artifacts/lead/{filename}"
        return f"artifacts/pin/{version}/{filename}"

    def inspect_artifact_remote(
        self, filename: str, *, version: str | None = None
    ) -> dict[str, Any]:
        """GET ``.../inspect`` for a stored artifact."""
        return self.request("GET", f"{self._artifact_prefix(filename, version=version)}/inspect")

    def list_symbols_remote(
        self, filename: str, *, version: str | None = None
    ) -> list[dict[str, Any]]:
        """GET ``.../symbols``."""
        data = self.request("GET", f"{self._artifact_prefix(filename, version=version)}/symbols")
        return list(data) if isinstance(data, list) else []

    def addr2line_remote(
        self, filename: str, addr: int, *, version: str | None = None
    ) -> list[dict[str, Any]]:
        """GET ``.../addr2line?addr=``."""
        data = self.request(
            "GET",
            f"{self._artifact_prefix(filename, version=version)}/addr2line",
            query={"addr": str(addr)},
        )
        return list(data) if isinstance(data, list) else []

    def locations_remote(
        self, filename: str, name: str, *, version: str | None = None
    ) -> list[dict[str, Any]]:
        """GET ``.../locations?name=``."""
        data = self.request(
            "GET",
            f"{self._artifact_prefix(filename, version=version)}/locations",
            query={"name": name},
        )
        return list(data) if isinstance(data, list) else []

    def disasm_remote(
        self,
        filename: str,
        index: int,
        *,
        offset: int = 0,
        limit: int = 64,
        version: str | None = None,
    ) -> list[dict[str, Any]]:
        """GET ``.../disasm?index=&offset=&limit=``."""
        data = self.request(
            "GET",
            f"{self._artifact_prefix(filename, version=version)}/disasm",
            query={
                "index": str(index),
                "offset": str(offset),
                "limit": str(limit),
            },
        )
        return list(data) if isinstance(data, list) else []

    def get_embedded_file(
        self, filename: str, path: str, *, version: str | None = None
    ) -> dict[str, Any]:
        """GET ``.../files?path=`` JSON view (text or binary stub)."""
        return self.request(
            "GET",
            f"{self._artifact_prefix(filename, version=version)}/files",
            query={"path": path},
        )

    def download_embedded_file(
        self, filename: str, path: str, *, version: str | None = None
    ) -> bytes:
        """GET ``.../files/raw?path=`` raw embedded bytes."""
        url_path = f"{self._artifact_prefix(filename, version=version)}/files/raw"
        headers: dict[str, str] = {"Accept": "application/octet-stream"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = Request(self._url(url_path, query={"path": path}), headers=headers, method="GET")
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                return resp.read()
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            detail = body
            try:
                parsed = json.loads(body)
                detail = str(parsed.get("detail", body))
            except json.JSONDecodeError:
                pass
            raise ClientError(detail, status=exc.code) from exc
        except URLError as exc:
            raise ClientError(f"connection failed: {exc.reason}") from exc

    def list_sections(
        self, filename: str, *, version: str | None = None
    ) -> list[Any]:
        """GET ``.../sections`` container section inventory."""
        return self.request(
            "GET", f"{self._artifact_prefix(filename, version=version)}/sections"
        )

    def download_section(
        self, filename: str, index: int, *, version: str | None = None
    ) -> bytes:
        """GET ``.../sections/raw?index=`` raw section payload bytes."""
        url_path = f"{self._artifact_prefix(filename, version=version)}/sections/raw"
        headers: dict[str, str] = {"Accept": "application/octet-stream"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = Request(
            self._url(url_path, query={"index": str(index)}),
            headers=headers,
            method="GET",
        )
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                return resp.read()
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            detail = body
            try:
                parsed = json.loads(body)
                detail = str(parsed.get("detail", body))
            except json.JSONDecodeError:
                pass
            raise ClientError(detail, status=exc.code) from exc
        except URLError as exc:
            raise ClientError(f"connection failed: {exc.reason}") from exc

    def list_trust(self) -> list[Any]:
        return self.request("GET", "admin/trust")

    def add_trust(self, cert_path: Path, *, name: str = "") -> dict[str, Any]:
        data = cert_path.read_bytes()
        return self.request(
            "POST",
            "admin/trust",
            multipart=[
                ("file", (cert_path.name, data, "application/octet-stream")),
                ("name", name or cert_path.name),
            ],
        )

    def delete_trust(self, root_id: str) -> None:
        self.request("DELETE", f"admin/trust/{root_id}", accept=None)

    def download_artifact(
        self,
        filename: str,
        *,
        version: str | None = None,
        if_none_match: str | None = None,
    ) -> ArtifactDownload:
        """Fetch lead or pin artifact; ``version=None`` means lead channel."""
        if version is None:
            path = f"artifacts/lead/{filename}"
        else:
            path = f"artifacts/pin/{version}/{filename}"
        headers: dict[str, str] = {"Accept": "application/octet-stream"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if if_none_match:
            headers["If-None-Match"] = if_none_match
        req = Request(self._url(path), headers=headers, method="GET")
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                etag = _header(resp.headers, "ETag") or None
                return ArtifactDownload(
                    data=resp.read(),
                    etag=etag,
                    not_modified=False,
                    status=getattr(resp, "status", 200) or 200,
                )
        except HTTPError as exc:
            if exc.code == 304:
                return ArtifactDownload(
                    data=None,
                    etag=_header(exc.headers, "ETag") or if_none_match,
                    not_modified=True,
                    status=304,
                )
            body = exc.read().decode("utf-8", errors="replace")
            detail = body
            try:
                parsed = json.loads(body)
                detail = str(parsed.get("detail", body))
            except json.JSONDecodeError:
                pass
            raise ClientError(detail, status=exc.code) from exc
        except URLError as exc:
            raise ClientError(f"connection failed: {exc.reason}") from exc


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
