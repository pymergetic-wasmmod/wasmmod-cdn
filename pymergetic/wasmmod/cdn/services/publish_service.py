"""Publish service — write artifacts into pin/lead channels."""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from pymergetic.wasmmod.cdn.layout import ChannelLayout, ChannelRef
from pymergetic.wasmmod.cdn.models import (
    Artifact,
    ArtifactEncoding,
    ArtifactKind,
    ChannelIndex,
    PackageEntry,
    PublishRequest,
    PublishResult,
)
from pymergetic.wasmmod.cdn.services.index_service import IndexService
from pymergetic.wasmmod.cdn.storage import ObjectStorage
from pymergetic.wasmmod.cdn_client.contents import ensure_zlib_artifacts, inspect_upload
from pymergetic.wasmmod.cdn_client.trust import SubcaPolicy
from pymergetic.wasmmod.cdn_client.verify import RequireSignedMode, enforce_signed_policy


class PublishService:
    """Write artifacts into pin and/or lead channels and refresh indexes."""

    def __init__(
        self,
        storage: ObjectStorage,
        indexes: IndexService,
        *,
        pin_immutable: bool = True,
        require_signed: RequireSignedMode = "off",
    ) -> None:
        self._storage = storage
        self._indexes = indexes
        self._layout = ChannelLayout()
        self._pin_immutable = pin_immutable
        self._require_signed: RequireSignedMode = require_signed

    async def set_successor(
        self,
        package: str,
        *,
        channel: ChannelRef,
        successor: str,
        deprecated: bool = True,
    ) -> PackageEntry:
        package = self._layout.validate_package_name(package)
        ChannelLayout.validate_package_name(successor.split("@", 1)[0])
        index = await self._indexes.load(channel)
        entry = index.packages.get(package)
        if entry is None:
            raise ValueError("package not found")
        updated = entry.model_copy(update={"successor": successor, "deprecated": deprecated})
        packages = dict(index.packages)
        packages[package] = updated
        await self._indexes.save(
            ChannelIndex(
                schema=1,
                channel=channel.name,
                generated=datetime.now(UTC),
                packages=packages,
            )
        )
        return updated

    async def publish(
        self,
        request: PublishRequest,
        files: dict[str, bytes],
        *,
        trust_roots: list[bytes] | None = None,
        subca_policy: SubcaPolicy | None = None,
    ) -> PublishResult:
        package = self._layout.validate_package_name(request.package)
        if not request.pin and not request.lead:
            raise ValueError("at least one of pin/lead must be true")
        if not files:
            raise ValueError("no artifact files provided")

        # CDN default: store MPZL (.wasm.zlib / .aotN.zlib); naked only if twin uploaded.
        files = ensure_zlib_artifacts(files)

        channels: list[ChannelRef] = []
        if request.pin:
            channels.append(self._layout.pin(request.version))
        if request.lead:
            channels.append(self._layout.lead())

        if self._pin_immutable and request.pin and not request.force:
            pin = self._layout.pin(request.version)
            existing = await self._indexes.get_package(pin, package)
            if existing is not None and not existing.yanked:
                raise ValueError(
                    f"pin @{request.version} already has package {package} "
                    "(immutable; pass force=true to overwrite)"
                )

        roots = trust_roots or []
        for filename, data in files.items():
            enforce_signed_policy(
                data,
                mode=self._require_signed,
                trust_roots=roots,
                filename=filename,
                subca_policy=subca_policy,
            )

        written: list[str] = []
        artifacts: list[Artifact] = []
        for filename, data in files.items():
            kind_s, arch, aot_ver, enc_s = self._layout.classify_artifact(filename)
            digest = hashlib.sha256(data).hexdigest()
            artifacts.append(
                Artifact(
                    path=filename,
                    kind=ArtifactKind(kind_s),
                    encoding=ArtifactEncoding(enc_s),
                    sha256=digest,
                    size=len(data),
                    arch=arch,
                    aot_version=aot_ver if kind_s == "aot" else request.aot_version,
                )
            )

        contents = inspect_upload(files)
        aot_version = request.aot_version or contents.aot_version
        deps = dict(request.deps)
        if not deps and contents.deps:
            deps = dict(contents.deps)

        index_paths: list[str] = []
        for channel in channels:
            for filename, data in files.items():
                key = channel.artifact_key(filename)
                await self._storage.put_bytes(key, data)
                written.append(key)

            index = await self._indexes.load(channel)
            prior = index.packages.get(package)
            entry = PackageEntry(
                version=request.version,
                aot_version=aot_version,
                deps=deps,
                artifacts=list(artifacts),
                maintainer_email=request.maintainer_email,
                description=request.description,
                homepage=request.homepage,
                license=request.license,
                yanked=False,
                yank_reason=None,
                deprecated=prior.deprecated if prior else False,
                successor=prior.successor if prior else None,
                contents=contents,
                updated_at=datetime.now(UTC),
            )
            packages = dict(index.packages)
            packages[package] = entry
            new_index = ChannelIndex(
                schema=1,
                channel=channel.name,
                generated=datetime.now(UTC),
                packages=packages,
            )
            index_paths.append(await self._indexes.save(new_index))

        return PublishResult(
            package=package,
            version=request.version,
            channels=[c.name for c in channels],
            index_paths=index_paths,
            artifacts=written,
            contents=contents,
        )

    async def promote(self, package: str, version: str) -> PublishResult:
        """Copy a pin package entry + artifacts into lead."""
        package = self._layout.validate_package_name(package)
        pin = self._layout.pin(version)
        lead = self._layout.lead()
        entry = await self._indexes.get_package(pin, package)
        if entry is None:
            raise ValueError(f"package {package} not found in @{version}")
        if entry.yanked:
            raise ValueError("cannot promote a yanked package")

        written: list[str] = []
        for art in entry.artifacts:
            src = pin.artifact_key(art.path)
            dst = lead.artifact_key(art.path)
            if not await self._storage.exists(src):
                raise ValueError(f"missing pin artifact: {src}")
            data = await self._storage.get_bytes(src)
            await self._storage.put_bytes(dst, data)
            written.append(dst)

        index = await self._indexes.load(lead)
        packages = dict(index.packages)
        packages[package] = entry.model_copy(update={"yanked": False, "yank_reason": None})
        index_path = await self._indexes.save(
            ChannelIndex(
                schema=1,
                channel=lead.name,
                generated=datetime.now(UTC),
                packages=packages,
            )
        )
        return PublishResult(
            package=package,
            version=entry.version,
            channels=[lead.name],
            index_paths=[index_path],
            artifacts=written,
        )

    async def yank(
        self,
        package: str,
        *,
        channel: ChannelRef,
        reason: str,
    ) -> PackageEntry:
        package = self._layout.validate_package_name(package)
        index = await self._indexes.load(channel)
        entry = index.packages.get(package)
        if entry is None:
            raise ValueError("package not found")
        updated = entry.model_copy(update={"yanked": True, "yank_reason": reason})
        packages = dict(index.packages)
        packages[package] = updated
        await self._indexes.save(
            ChannelIndex(
                schema=1,
                channel=channel.name,
                generated=datetime.now(UTC),
                packages=packages,
            )
        )
        return updated
