"""Object storage for channel state (packs + index.json)."""

from __future__ import annotations

import asyncio
import hashlib
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

import aiofiles

if TYPE_CHECKING:
    from pymergetic.metal.cdn.settings import Settings


class ObjectStorage(ABC):
    """Async blob store keyed by relative channel paths."""

    @abstractmethod
    async def put_bytes(self, key: str, data: bytes) -> None: ...

    @abstractmethod
    async def get_bytes(self, key: str) -> bytes: ...

    @abstractmethod
    async def exists(self, key: str) -> bool: ...

    @abstractmethod
    async def delete(self, key: str) -> None: ...

    @abstractmethod
    async def list_keys(self, prefix: str = "") -> list[str]: ...

    async def put_file(self, key: str, path: Path) -> None:
        data = await asyncio.to_thread(path.read_bytes)
        await self.put_bytes(key, data)

    async def sha256_of(self, key: str) -> str:
        data = await self.get_bytes(key)
        return hashlib.sha256(data).hexdigest()

    async def presign_put(self, key: str, *, expires_in: int = 3600) -> str:
        raise NotImplementedError("presigned PUT not supported by this backend")

    async def presign_get(self, key: str, *, expires_in: int = 3600) -> str:
        raise NotImplementedError("presigned GET not supported by this backend")


class LocalObjectStorage(ObjectStorage):
    """Filesystem backend under ``storage_root`` (default packs tree)."""

    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        cleaned = key.lstrip("/")
        path = (self.root / cleaned).resolve()
        if not str(path).startswith(str(self.root)):
            raise ValueError(f"key escapes storage root: {key}")
        return path

    async def put_bytes(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)

        async def _write() -> None:
            async with aiofiles.open(path, "wb") as fh:
                await fh.write(data)

        await _write()

    async def get_bytes(self, key: str) -> bytes:
        path = self._path(key)
        async with aiofiles.open(path, "rb") as fh:
            return await fh.read()

    async def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    async def delete(self, key: str) -> None:
        path = self._path(key)
        if path.is_file():
            await asyncio.to_thread(path.unlink)

    async def list_keys(self, prefix: str = "") -> list[str]:
        base = self._path(prefix) if prefix else self.root
        if base.is_file():
            return [prefix.lstrip("/")]
        if not base.exists():
            return []

        def _walk() -> list[str]:
            out: list[str] = []
            for p in base.rglob("*"):
                if p.is_file():
                    out.append(str(p.relative_to(self.root)).replace("\\", "/"))
            return sorted(out)

        return await asyncio.to_thread(_walk)

    def resolve_public_path(self, key: str) -> Path | None:
        path = self._path(key)
        return path if path.is_file() else None

    async def presign_put(self, key: str, *, expires_in: int = 3600) -> str:
        del expires_in
        # Local/dev: client still uploads via the app; return a marker URL.
        return f"local://put/{quote(key, safe='/')}"

    async def presign_get(self, key: str, *, expires_in: int = 3600) -> str:
        del expires_in
        return f"local://get/{quote(key, safe='/')}"


class S3ObjectStorage(ObjectStorage):
    """S3 / MinIO backend via aiobotocore."""

    def __init__(
        self,
        *,
        bucket: str,
        endpoint_url: str | None,
        access_key: str,
        secret_key: str,
        region: str = "us-east-1",
    ) -> None:
        self.bucket = bucket
        self.endpoint_url = endpoint_url
        self.access_key = access_key
        self.secret_key = secret_key
        self.region = region
        self._session: Any = None

    @asynccontextmanager
    async def _client(self) -> AsyncIterator[Any]:
        try:
            import aioboto3
        except ImportError as exc:
            raise RuntimeError(
                "S3 backend requires aioboto3 — pip install 'pymergetic-metal-cdn[s3]'"
            ) from exc
        if self._session is None:
            self._session = aioboto3.Session()
        async with self._session.client(
            "s3",
            endpoint_url=self.endpoint_url,
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
            region_name=self.region,
        ) as client:
            yield client

    async def put_bytes(self, key: str, data: bytes) -> None:
        async with self._client() as client:
            await client.put_object(Bucket=self.bucket, Key=key, Body=data)

    async def get_bytes(self, key: str) -> bytes:
        async with self._client() as client:
            resp = await client.get_object(Bucket=self.bucket, Key=key)
            body = resp["Body"]
            return await body.read()

    async def exists(self, key: str) -> bool:
        try:
            async with self._client() as client:
                await client.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:  # noqa: BLE001 — missing key / network
            return False

    async def delete(self, key: str) -> None:
        async with self._client() as client:
            await client.delete_object(Bucket=self.bucket, Key=key)

    async def list_keys(self, prefix: str = "") -> list[str]:
        out: list[str] = []
        async with self._client() as client:
            token: str | None = None
            while True:
                kwargs: dict[str, object] = {"Bucket": self.bucket, "Prefix": prefix}
                if token:
                    kwargs["ContinuationToken"] = token
                resp = await client.list_objects_v2(**kwargs)
                for obj in resp.get("Contents") or []:
                    out.append(obj["Key"])
                if not resp.get("IsTruncated"):
                    break
                token = resp.get("NextContinuationToken")
        return sorted(out)

    async def presign_put(self, key: str, *, expires_in: int = 3600) -> str:
        async with self._client() as client:
            return await client.generate_presigned_url(
                "put_object",
                Params={"Bucket": self.bucket, "Key": key},
                ExpiresIn=expires_in,
            )

    async def presign_get(self, key: str, *, expires_in: int = 3600) -> str:
        async with self._client() as client:
            return await client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": key},
                ExpiresIn=expires_in,
            )


def build_storage(settings: Settings) -> ObjectStorage:
    backend = (settings.storage_backend or "local").lower()
    if backend == "local":
        return LocalObjectStorage(settings.storage_root)
    if backend in ("s3", "minio"):
        if not settings.s3_bucket or not settings.s3_access_key or not settings.s3_secret_key:
            raise ValueError("S3 backend requires METAL_CDN_S3_BUCKET / ACCESS_KEY / SECRET_KEY")
        return S3ObjectStorage(
            bucket=settings.s3_bucket,
            endpoint_url=settings.s3_endpoint,
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key,
            region=settings.s3_region,
        )
    raise ValueError(f"unknown storage backend: {backend}")


async def collect_orphan_keys(storage: ObjectStorage) -> list[str]:
    """Keys not referenced by any index.json artifact path or the index itself."""
    keys = await storage.list_keys()
    referenced: set[str] = set()
    for key in keys:
        if key == "index.json" or key.endswith("/index.json"):
            referenced.add(key)
            raw = await storage.get_bytes(key)
            # Lazy parse — avoid circular import of ChannelIndex at module import time.
            from pymergetic.metal.cdn.models import ChannelIndex

            index = ChannelIndex.model_validate_json(raw)
            prefix = "" if key == "index.json" else key[: -len("index.json")]
            for entry in index.packages.values():
                for art in entry.artifacts:
                    referenced.add(f"{prefix}{art.path}")
    return sorted(k for k in keys if k not in referenced)
