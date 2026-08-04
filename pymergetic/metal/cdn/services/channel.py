"""Index + publish services over object storage (channel state)."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime

from pymergetic.metal.cdn.layout import ChannelLayout, ChannelRef
from pymergetic.metal.cdn.models import (
    Artifact,
    ArtifactEncoding,
    ArtifactKind,
    ChannelIndex,
    ChannelSummary,
    ChannelTreeNode,
    MaintainerSummary,
    PackageEntry,
    PackageNavNode,
    PackageSummary,
    PackageVersionOption,
    PublishRequest,
    PublishResult,
)
from pymergetic.metal.cdn.storage import ObjectStorage
from pymergetic.metal.cdn_client.contents import ensure_zlib_artifacts, inspect_upload
from pymergetic.metal.cdn_client.verify import RequireSignedMode, enforce_signed_policy


def sign_index(index: ChannelIndex, key: str) -> str:
    """HMAC-SHA256 over canonical JSON without the signature field."""
    body = index.model_dump(by_alias=True, mode="json", exclude={"signature"})
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    secret = key.encode("utf-8")
    return hmac.new(secret, canonical, hashlib.sha256).hexdigest()


def verify_index_signature(index: ChannelIndex, key: str) -> bool:
    if not index.signature:
        return False
    expected = sign_index(index, key)
    return hmac.compare_digest(expected, index.signature)


class IndexService:
    def __init__(self, storage: ObjectStorage, *, signing_key: str | None = None) -> None:
        self._storage = storage
        self._signing_key = signing_key

    async def load(self, channel: ChannelRef) -> ChannelIndex:
        key = channel.index_key()
        if not await self._storage.exists(key):
            return ChannelIndex(
                schema=1,
                channel=channel.name,
                generated=datetime.now(UTC),
                packages={},
            )
        raw = await self._storage.get_bytes(key)
        return ChannelIndex.model_validate_json(raw)

    async def save(self, index: ChannelIndex) -> str:
        channel = ChannelRef(index.channel)
        key = channel.index_key()
        to_store = index
        if self._signing_key:
            to_store = index.model_copy(update={"signature": sign_index(index, self._signing_key)})
        payload = to_store.model_dump_json(by_alias=True, indent=2).encode("utf-8")
        await self._storage.put_bytes(key, payload)
        return key

    async def discover_channels(self) -> list[ChannelRef]:
        """Find channels that have an index.json in object storage."""
        keys = await self._storage.list_keys()
        found: set[str] = set()
        for key in keys:
            if key == "index.json":
                found.add("lead")
            elif key.endswith("/index.json") and key.startswith("@"):
                found.add(key[: -len("/index.json")])
        refs: list[ChannelRef] = [ChannelLayout.lead()]
        found.discard("lead")
        for name in sorted(found):
            refs.append(ChannelRef(name))
        return refs

    async def list_packages(
        self,
        channel: ChannelRef,
        *,
        include_yanked: bool = True,
    ) -> list[PackageSummary]:
        index = await self.load(channel)
        out: list[PackageSummary] = []
        for name, entry in sorted(index.packages.items()):
            if entry.yanked and not include_yanked:
                continue
            out.append(
                PackageSummary(
                    name=name,
                    version=entry.version,
                    channel=channel.name,
                    artifact_count=len(entry.artifacts),
                    maintainer_email=entry.maintainer_email,
                    description=entry.description,
                    yanked=entry.yanked,
                    deprecated=entry.deprecated,
                    successor=entry.successor,
                    license=entry.license,
                    homepage=entry.homepage,
                    updated_at=entry.updated_at or index.generated,
                    version_count=1,
                )
            )
        return out

    async def list_catalog(self, *, include_yanked: bool = True) -> list[PackageSummary]:
        """Flat FQN catalog: one row per package name across lead + pins.

        Prefer lead metadata when present. ``updated_at`` is the newest publish
        (or channel index ``generated`` for legacy entries). Sorted newest first.
        ``needed_by`` is reverse deps from other catalog packages' ``[deps]``.
        """
        groups: dict[str, list[tuple[ChannelRef, PackageEntry, datetime]]] = {}
        for channel in await self.discover_channels():
            index = await self.load(channel)
            for name, entry in index.packages.items():
                if entry.yanked and not include_yanked:
                    continue
                groups.setdefault(name, []).append((channel, entry, index.generated))

        primaries: dict[str, tuple[ChannelRef, PackageEntry, datetime]] = {}
        for name, items in groups.items():
            lead = next((i for i in items if i[0].is_lead), None)
            primaries[name] = lead if lead is not None else max(
                items,
                key=lambda i: i[1].updated_at or i[2],
            )

        # Exact versions available (any channel), ignoring yanked pins.
        available: dict[str, set[str]] = {}
        for pkg_name, items in groups.items():
            for _, entry, _ in items:
                if not entry.yanked:
                    available.setdefault(pkg_name, set()).add(entry.version)

        # Reverse edges from primary (usually lead) [deps].
        needed_by: dict[str, set[str]] = {n: set() for n in primaries}
        for name, (_, primary, _) in primaries.items():
            for dep_name in primary.deps:
                needed_by.setdefault(dep_name, set()).add(name)

        out: list[PackageSummary] = []
        for name, items in groups.items():
            primary_ch, primary, _ = primaries[name]
            updated = max((e.updated_at or gen for _, e, gen in items))
            deps = dict(primary.deps)
            deps_ok = {
                dep: (req in available.get(dep, ())) for dep, req in deps.items()
            }
            out.append(
                PackageSummary(
                    name=name,
                    version=primary.version,
                    channel=primary_ch.name,
                    artifact_count=len(primary.artifacts),
                    maintainer_email=primary.maintainer_email,
                    description=primary.description,
                    yanked=primary.yanked,
                    deprecated=primary.deprecated,
                    successor=primary.successor,
                    license=primary.license,
                    homepage=primary.homepage,
                    updated_at=updated,
                    version_count=len(items),
                    deps=deps,
                    deps_ok=deps_ok,
                    needed_by=sorted(needed_by.get(name, ())),
                )
            )
        out.sort(key=lambda s: s.name)
        out.sort(
            key=lambda s: s.updated_at or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        )
        return out

    async def list_dependents(self, name: str) -> list[str]:
        """Published packages that declare ``name`` in ``[deps]`` (catalog reverse)."""
        catalog = await self.list_catalog()
        for row in catalog:
            if row.name == name:
                return list(row.needed_by)
        return sorted(row.name for row in catalog if name in row.deps)

    async def deps_fit(self, deps: dict[str, str]) -> dict[str, bool]:
        """Exact version present on some non-yanked channel for each dep name."""
        if not deps:
            return {}
        available: dict[str, set[str]] = {}
        for channel in await self.discover_channels():
            index = await self.load(channel)
            for pkg_name, entry in index.packages.items():
                if not entry.yanked:
                    available.setdefault(pkg_name, set()).add(entry.version)
        return {dep: (req in available.get(dep, ())) for dep, req in deps.items()}

    async def search(
        self,
        query: str,
        *,
        channel: ChannelRef | None = None,
        include_yanked: bool = False,
    ) -> list[PackageSummary]:
        q = query.strip().lower()
        channels = [channel] if channel is not None else await self.discover_channels()
        hits: list[PackageSummary] = []
        for ref in channels:
            for summary in await self.list_packages(ref, include_yanked=include_yanked):
                hay = " ".join(
                    filter(
                        None,
                        [
                            summary.name,
                            summary.version,
                            summary.description or "",
                            str(summary.maintainer_email or ""),
                        ],
                    )
                ).lower()
                if not q or q in hay:
                    hits.append(summary)
        return hits

    async def get_package(self, channel: ChannelRef, name: str) -> PackageEntry | None:
        index = await self.load(channel)
        return index.packages.get(name)

    async def browse_tree(self) -> list[ChannelTreeNode]:
        nodes: list[ChannelTreeNode] = []
        for channel in await self.discover_channels():
            packages = await self.list_packages(channel)
            nodes.append(
                ChannelTreeNode(
                    channel=ChannelSummary(
                        name=channel.name,
                        package_count=len(packages),
                        is_lead=channel.is_lead,
                    ),
                    packages=packages,
                )
            )
        return nodes

    async def packages_by_maintainer(
        self, email: str, *, channel: ChannelRef | None = None
    ) -> list[PackageSummary]:
        """Packages whose ``maintainer_email`` matches (lead by default)."""
        needle = email.strip().lower()
        refs = [channel] if channel is not None else [ChannelLayout.lead()]
        # Also scan pins if lead-only empty? Prefer lead catalog for author page.
        out: list[PackageSummary] = []
        seen: set[str] = set()
        for ref in refs:
            for pkg in await self.list_packages(ref):
                if (pkg.maintainer_email or "").lower() != needle:
                    continue
                if pkg.name in seen:
                    continue
                seen.add(pkg.name)
                out.append(pkg)
        return out

    async def list_maintainers(
        self, *, channel: ChannelRef | None = None
    ) -> list[MaintainerSummary]:
        """Distinct maintainer emails on a channel (lead by default), sorted."""
        ref = channel if channel is not None else ChannelLayout.lead()
        counts: dict[str, int] = {}
        display: dict[str, str] = {}
        for pkg in await self.list_packages(ref):
            email = (pkg.maintainer_email or "").strip()
            if not email:
                continue
            key = email.lower()
            display.setdefault(key, email)
            counts[key] = counts.get(key, 0) + 1
        return [
            MaintainerSummary(email=display[key], package_count=counts[key])
            for key in sorted(counts)
        ]

    async def package_versions(self, name: str) -> list[PackageVersionOption]:
        """All channels that publish ``name``, lead first then pins (newest label)."""
        options: list[PackageVersionOption] = []
        lead: PackageVersionOption | None = None
        pins: list[PackageVersionOption] = []
        for channel in await self.discover_channels():
            entry = await self.get_package(channel, name)
            if entry is None:
                continue
            opt = PackageVersionOption(
                channel=channel.name,
                version=entry.version,
                label=(
                    f"lead ({entry.version})"
                    if channel.is_lead
                    else f"{channel.name.lstrip('@')} ({entry.version})"
                ),
                artifact_count=len(entry.artifacts),
            )
            if channel.is_lead:
                lead = opt
            else:
                pins.append(opt)
        pins.sort(key=lambda o: o.version, reverse=True)
        if lead is not None:
            options.append(lead)
        options.extend(pins)
        return options

    async def browse_package_nav(self) -> list[PackageNavNode]:
        """Package-centric collapsible tree (dots / slash prefixes → folders)."""
        by_name: dict[str, list[PackageVersionOption]] = {}
        for channel in await self.discover_channels():
            for pkg in await self.list_packages(channel):
                opt = PackageVersionOption(
                    channel=pkg.channel,
                    version=pkg.version,
                    label=(
                        f"lead ({pkg.version})"
                        if pkg.channel == "lead"
                        else f"{pkg.channel.lstrip('@')} ({pkg.version})"
                    ),
                    artifact_count=pkg.artifact_count,
                )
                by_name.setdefault(pkg.name, []).append(opt)

        for name, opts in by_name.items():
            lead = [o for o in opts if o.channel == "lead"]
            pins = sorted(
                [o for o in opts if o.channel != "lead"],
                key=lambda o: o.version,
                reverse=True,
            )
            by_name[name] = lead + pins

        roots: list[PackageNavNode] = []
        for full_name in sorted(by_name.keys()):
            parts = (
                full_name.split(".")
                if "." in full_name
                else full_name.split("/")
            )
            self._nav_insert(roots, parts, full_name, by_name[full_name])
        return roots

    @staticmethod
    def _nav_insert(
        siblings: list[PackageNavNode],
        parts: list[str],
        full_name: str,
        versions: list[PackageVersionOption],
    ) -> None:
        if not parts:
            return
        head, *rest = parts
        if not rest:
            for node in siblings:
                if node.name == head:
                    node.full_name = full_name
                    node.versions = versions
                    return
            siblings.append(
                PackageNavNode(name=head, full_name=full_name, versions=versions)
            )
            return
        folder: PackageNavNode | None = None
        for node in siblings:
            if node.name == head:
                folder = node
                break
        if folder is None:
            folder = PackageNavNode(name=head)
            siblings.append(folder)
        IndexService._nav_insert(folder.children, rest, full_name, versions)

    @staticmethod
    def parse_channel(channel: str) -> ChannelRef:
        if channel in ("", "lead", "latest"):
            return ChannelLayout.lead()
        if channel.startswith("pin/"):
            return ChannelLayout.pin(channel.removeprefix("pin/"))
        if channel.startswith("@"):
            return ChannelLayout.pin(channel)
        return ChannelLayout.pin(channel)


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
