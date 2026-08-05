"""Merge local catalog with federated peer package lists."""

from __future__ import annotations

import logging
import time

from fastapi import Request
from pydantic import ValidationError

from pymergetic.metal.cdn.models import PackageNavNode, PackageSummary, PackageVersionOption
from pymergetic.metal.cdn.services.channel import IndexService
from pymergetic.metal.cdn.services.federation.forward import forward_json
from pymergetic.metal.cdn.services.federation.prefix import name_under_prefix
from pymergetic.metal.cdn.services.federation.proxy import FederationProxy
from pymergetic.metal.cdn.services.federation.registry import FederationRegistry
from pymergetic.metal.cdn.services.federation.tables import FederationDirection, FederationMountRead

log = logging.getLogger("metal_cdn.federation.catalog")

_DEFAULT_TTL_S = 30.0


class PeerCatalogCache:
    """Short TTL cache of peer ``GET /packages`` rows (keyed by mount id)."""

    def __init__(self, ttl_s: float = _DEFAULT_TTL_S) -> None:
        self._ttl = ttl_s
        self._rows: dict[str, tuple[float, list[PackageSummary]]] = {}

    def get(self, mount_id: str) -> list[PackageSummary] | None:
        hit = self._rows.get(mount_id)
        if hit is None:
            return None
        expires, rows = hit
        if time.monotonic() > expires:
            self._rows.pop(mount_id, None)
            return None
        return rows

    def put(self, mount_id: str, rows: list[PackageSummary]) -> None:
        self._rows[mount_id] = (time.monotonic() + self._ttl, rows)


async def fetch_mount_packages(
    *,
    proxy: FederationProxy,
    reg: FederationRegistry,
    mount: FederationMountRead,
    request: Request,
    cache: PeerCatalogCache | None,
    prefix: str | None = None,
) -> list[PackageSummary]:
    # Cache key includes prefix so filtered and full catalogs don't collide.
    key = f"{mount.id}:{prefix or ''}"
    if cache is not None:
        cached = cache.get(key)
        if cached is not None:
            return list(cached)
    params: dict[str, str] = {"channel": "lead", "include_yanked": "true"}
    if prefix:
        params["prefix"] = prefix
    try:
        data = await forward_json(
            proxy=proxy,
            reg=reg,
            mount=mount,
            path="/packages",
            request=request,
            params=params,
        )
    except Exception as exc:
        log.warning("federation catalog fetch failed mount=%s: %s", mount.prefix, exc)
        return []
    if not isinstance(data, list):
        return []
    browse_base = (mount.peer_base_url or "").rstrip("/")
    out: list[PackageSummary] = []
    for raw in data:
        try:
            row = PackageSummary.model_validate(raw)
        except ValidationError:
            continue
        if not name_under_prefix(row.name, mount.prefix):
            continue
        if prefix and not name_under_prefix(row.name, prefix):
            continue
        browse = f"{browse_base}/channels/lead/packs/{row.name}" if browse_base else None
        out.append(
            row.model_copy(
                update={
                    "origin": "remote",
                    "mount_prefix": mount.prefix,
                    "peer_label": mount.peer_label,
                    "peer_browse_url": browse,
                }
            )
        )
    if cache is not None:
        cache.put(key, out)
    return out


async def merge_catalog(
    local: list[PackageSummary],
    *,
    reg: FederationRegistry,
    proxy: FederationProxy,
    request: Request,
    cache: PeerCatalogCache | None = None,
    prefix: str | None = None,
) -> list[PackageSummary]:
    """Append remote packages under enabled pull mounts; local names win."""
    local_names = {p.name for p in local}
    mounts = [
        m
        for m in await reg.list_mounts()
        if m.enabled and m.direction == FederationDirection.PULL
    ]
    if prefix:
        mounts = [
            m
            for m in mounts
            if name_under_prefix(prefix, m.prefix)
            or name_under_prefix(m.prefix, prefix)
            or prefix == m.prefix
        ]
    remote_rows: list[PackageSummary] = []
    for mount in mounts:
        for row in await fetch_mount_packages(
            proxy=proxy,
            reg=reg,
            mount=mount,
            request=request,
            cache=cache,
            prefix=prefix,
        ):
            if row.name in local_names:
                continue
            remote_rows.append(row)
            local_names.add(row.name)
    if not remote_rows:
        return local
    merged = list(local) + remote_rows
    merged.sort(key=lambda s: s.name)
    merged.sort(
        key=lambda s: (s.updated_at.timestamp() if s.updated_at else 0.0),
        reverse=True,
    )
    return merged


def nav_from_catalog(catalog: list[PackageSummary]) -> list[PackageNavNode]:
    """Build sidebar nav from a (possibly federated) flat catalog."""
    by_name: dict[str, list[PackageVersionOption]] = {}
    origins: dict[str, tuple[str, str | None]] = {}
    for pkg in catalog:
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
        origins[pkg.name] = (pkg.origin, pkg.peer_browse_url)

    roots: list[PackageNavNode] = []
    for full_name in sorted(by_name.keys()):
        parts = full_name.split(".") if "." in full_name else full_name.split("/")
        IndexService._nav_insert(roots, parts, full_name, by_name[full_name])
    _tag_nav_origins(roots, origins)
    return roots


def _tag_nav_origins(
    nodes: list[PackageNavNode],
    origins: dict[str, tuple[str, str | None]],
) -> None:
    for node in nodes:
        if node.full_name and node.full_name in origins:
            origin, browse = origins[node.full_name]
            node.origin = origin
            node.peer_browse_url = browse
        if node.children:
            _tag_nav_origins(node.children, origins)
        if node.is_folder and not node.is_package and _all_remote_descendants(node):
            node.origin = "remote"


def _all_remote_descendants(node: PackageNavNode) -> bool:
    pkgs: list[PackageNavNode] = []

    def walk(n: PackageNavNode) -> None:
        if n.is_package:
            pkgs.append(n)
        for c in n.children:
            walk(c)

    walk(node)
    return bool(pkgs) and all(p.origin == "remote" for p in pkgs)


async def enrich_shell_lists(
    request: Request,
    *,
    reg: FederationRegistry,
    proxy: FederationProxy,
    catalog: list[PackageSummary],
) -> tuple[list[PackageSummary], list[PackageNavNode]]:
    cache = getattr(request.app.state, "federation_catalog_cache", None)
    if cache is None:
        cache = PeerCatalogCache()
        request.app.state.federation_catalog_cache = cache
    merged = await merge_catalog(
        catalog, reg=reg, proxy=proxy, request=request, cache=cache
    )
    return merged, nav_from_catalog(merged)
