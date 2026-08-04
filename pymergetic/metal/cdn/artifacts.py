"""Injectable artifact byte source (S3 today; VFS/modules later).

Keep FastAPI routes thin: resolve bytes via :class:`ArtifactStore`, then call
``cdn_client.contents`` / ``verify``. A Microdot + local VFS port can implement
the same protocol without touching inspect logic.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pymergetic.metal.cdn.layout import ChannelLayout
from pymergetic.metal.cdn.storage import ObjectStorage


@runtime_checkable
class ArtifactStore(Protocol):
    async def get_bytes(self, *, channel: str, filename: str) -> bytes: ...

    async def exists(self, *, channel: str, filename: str) -> bool: ...


class StorageArtifactStore:
    """``ObjectStorage``-backed channel artifacts (lead / @pin)."""

    def __init__(self, storage: ObjectStorage) -> None:
        self._storage = storage

    def _key(self, channel: str, filename: str) -> str:
        if channel == "lead":
            return ChannelLayout.lead().artifact_key(filename)
        if channel.startswith("@"):
            return ChannelLayout.pin(channel[1:]).artifact_key(filename)
        return ChannelLayout.pin(channel).artifact_key(filename)

    async def get_bytes(self, *, channel: str, filename: str) -> bytes:
        return await self._storage.get_bytes(self._key(channel, filename))

    async def exists(self, *, channel: str, filename: str) -> bool:
        return await self._storage.exists(self._key(channel, filename))
