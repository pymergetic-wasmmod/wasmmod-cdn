"""Exact-deps install order (``name`` / ``name@version`` roots)."""

from __future__ import annotations

from collections import deque

from pymergetic.wasmmod.cdn.models import ChannelIndex, PackageEntry


def parse_root(root: str) -> tuple[str, str | None]:
    """Split ``name`` or ``name@version`` into (package, pin_version|None)."""
    if "@" in root and not root.startswith("@"):
        name, _, ver = root.partition("@")
        if name and ver:
            return name, ver
    return root, None


def resolve_install_order(
    root: str,
    *,
    lead: ChannelIndex,
    pins: dict[str, ChannelIndex] | None = None,
) -> list[tuple[str, str]]:
    """Return install order ``[(name, version), …]`` using exact deps (Kahn).

    Root ``name`` resolves against *lead*; ``name@version`` against pin
    ``@version`` (must be present in *pins* keyed by version string without ``@``).
    """
    pins = pins or {}
    name, pin_ver = parse_root(root)

    def entry_for(pkg: str, want_ver: str | None) -> PackageEntry:
        if want_ver is not None:
            index = pins.get(want_ver)
            if index is not None:
                ent = index.packages.get(pkg)
                if ent is not None:
                    return ent
            # Exact dep may already sit on lead at that version.
            ent = lead.packages.get(pkg)
            if ent is not None and ent.version == want_ver:
                return ent
            if index is None:
                raise ValueError(f"missing pin index for @{want_ver} (and not on lead)")
            raise ValueError(f"package {pkg}@{want_ver} not found")
        ent = lead.packages.get(pkg)
        if ent is None:
            raise ValueError(f"package {pkg} not found on lead")
        return ent

    resolved: dict[str, str] = {}
    deps_map: dict[str, dict[str, str]] = {}

    def visit(pkg: str, want_ver: str | None) -> None:
        if pkg in resolved:
            if want_ver is not None and resolved[pkg] != want_ver:
                raise ValueError(f"version conflict for {pkg}: {resolved[pkg]} vs {want_ver}")
            return
        ent = entry_for(pkg, want_ver)
        if ent.yanked:
            if ent.successor:
                succ_name, succ_ver = parse_root(ent.successor)
                visit(succ_name, succ_ver)
                return
            raise ValueError(f"package {pkg}@{ent.version} is yanked")
        resolved[pkg] = ent.version
        deps_map[pkg] = dict(ent.deps)
        for dep_name, dep_ver in ent.deps.items():
            visit(dep_name, dep_ver)

    visit(name, pin_ver)

    indeg = {p: 0 for p in resolved}
    edges: dict[str, list[str]] = {p: [] for p in resolved}
    for pkg, deps in deps_map.items():
        for dep in deps:
            if dep not in indeg:
                raise ValueError(f"unresolved dependency {dep} of {pkg}")
            edges[dep].append(pkg)
            indeg[pkg] += 1

    queue = deque(sorted(p for p, d in indeg.items() if d == 0))
    order: list[tuple[str, str]] = []
    while queue:
        pkg = queue.popleft()
        order.append((pkg, resolved[pkg]))
        for nxt in sorted(edges[pkg]):
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                queue.append(nxt)
    if len(order) != len(resolved):
        raise ValueError("dependency cycle detected")
    return order
