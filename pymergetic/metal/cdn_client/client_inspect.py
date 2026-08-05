"""Remote artifact inspect / embed / section APIs for CdnClient."""

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pymergetic.metal.cdn_client.http_util import ClientError


class ArtifactInspectMixin:
    """Mixin: requires ``request``, ``_url``, ``token``, ``timeout`` on ``self``."""

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

    def mpy_disasm_remote(
        self,
        filename: str,
        path: str,
        *,
        limit: int = 80,
        version: str | None = None,
    ) -> list[dict[str, Any]]:
        """GET ``.../files/mpy-disasm?path=&limit=``."""
        data = self.request(
            "GET",
            f"{self._artifact_prefix(filename, version=version)}/files/mpy-disasm",
            query={"path": path, "limit": str(limit)},
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

    def list_sections(self, filename: str, *, version: str | None = None) -> list[Any]:
        """GET ``.../sections`` container section inventory."""
        return self.request("GET", f"{self._artifact_prefix(filename, version=version)}/sections")

    def download_section(self, filename: str, index: int, *, version: str | None = None) -> bytes:
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
