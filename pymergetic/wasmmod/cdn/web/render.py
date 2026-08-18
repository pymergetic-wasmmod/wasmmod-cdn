"""utemplate render layer for the shared browse UI (CDN side).

Replaces Jinja2Templates with the same micro utemplate engine the on-device
(metal) seat uses, so both sides render literally the same template sources —
one UI source. The pfalcon/utemplate dialect has no ``extends``/``block``/
``macro``, so the layout lives in ``templates/shell.html`` (a real template,
with the page body + precomputed package tree passed in as strings). Only the
recursive nav-tree builder (which had to be a Jinja macro) is Python — the
dialect cannot express recursion in a template.
"""

from __future__ import annotations

from pathlib import Path

from pymergetic.wasmmod.cdn import __version__
from pymergetic.wasmmod.cdn.paths import join_base

_base_path: str = "/"


def configure_web(base_path: str) -> None:
    global _base_path
    _base_path = base_path if base_path in ("", "/") else base_path.rstrip("/")


def href(*parts: str) -> str:
    return join_base(_base_path, *parts)


def _url(*parts: str) -> str:
    return join_base(_base_path, *parts)


TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


class _FSTemplateLoader:
    """Standalone utemplate loader over the templates directory.

    ``source.Loader`` assumes templates live in an importable package and hands
    the compiled files to its pkg-based ``compiled.Loader`` via ``__import__``.
    We render from an arbitrary filesystem directory instead, so we compile to a
    sibling ``_compiled`` dir and import the generated module by file path. Nested
    ``{% include %}`` calls the Compiler's ``loader.input_open``/``compiled_path``,
    which this loader provides.
    """

    def __init__(self, template_dir: Path, compiled_dir: Path):
        self.template_dir = template_dir
        self.compiled_dir = compiled_dir
        self.compiled_dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, object] = {}

    def input_open(self, template: str):
        return open(self.template_dir / template)

    def compiled_path(self, template: str):
        return self.compiled_dir / (template.replace(".", "_") + ".py")

    def _import(self, name: str):
        import importlib.util
        mod = self._cache.get(name)
        if mod is None:
            path = self.compiled_path(name)
            spec = importlib.util.spec_from_file_location(f"_utcompiled_{name.replace('.', '_')}", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            self._cache[name] = mod
        return mod

    def render(self, name: str, ctx: dict) -> str:
        out = self.compiled_path(name)
        src = self.template_dir / name
        # Recompile when the compiled file is missing or older than the source.
        src_stat = src.stat()
        try:
            out_stat = out.stat()
        except OSError:
            out_stat = None
        if out_stat is None or out_stat.st_mtime < src_stat.st_mtime:
            with src.open("r") as f_in, out.open("w") as f_out:
                from pymergetic.wasmmod.cdn.web import _utemplate
                c = _utemplate.source.Compiler(f_in, f_out, loader=self)
                c.compile()
        mod = self._import(name)
        return "".join(mod.render(ctx))


_loader = None


def _get_loader():
    global _loader
    if _loader is None:
        _loader = _FSTemplateLoader(TEMPLATES_DIR, TEMPLATES_DIR / "_compiled")
    return _loader


def render_raw(name: str, ctx: dict) -> str:
    loader = _get_loader()
    return loader.render(name, ctx)


def _esc(v):
    import html as _h
    return _h.escape(str(v) if v is not None else "", quote=True)


# package-tree builder — recursive, so it lives in Python (was a Jinja macro).
def _tree_row(node: dict, active_package: str, active_channel: str) -> str:
    parts = ['<div class="tree-row">']
    if node.get("is_folder"):
        parts.append('<button type="button" class="tree-toggle" aria-expanded="false"><span class="tree-chevron" aria-hidden="true"></span></button>')
    else:
        parts.append('<span class="tree-toggle-spacer" aria-hidden="true"></span>')
    name = _esc(node.get("name", ""))
    full_name = node.get("full_name")
    role = node.get("role")
    versions = node.get("versions") or []
    default_ch = versions[0].get("channel", "lead") if versions else "lead"
    active_here = bool(full_name and active_package == full_name)
    pkg_href = _url("channels", (active_channel if active_here and versions else default_ch), "packs", full_name) if full_name else "#"
    if node.get("is_package"):
        classes = "tree-pkg tree-name" + (" is-active" if active_here else "")
        parts.append(f'<a class="{classes}" href="{pkg_href}" data-package="{_esc(full_name)}">{name}</a>')
        if role:
            parts.append(_role_svg(role))
        if len(versions) > 1:
            parts.append(_ver_select(full_name, versions, active_package, active_channel))
        elif versions:
            parts.append(f'<span class="tree-ver">{_esc(versions[0].get("version", ""))}</span>')
    elif node.get("is_folder"):
        parts.append(f'<span class="tree-name">{name}</span>')
    else:
        parts.append(f'<span class="tree-name">{name}</span>')
    parts.append(f'<span class="tree-count">{len(node.get("children") or [])}</span>')
    parts.append("</div>")
    return "".join(parts)


def _ver_select(full_name: str, versions: list, active_package: str, active_channel: str) -> str:
    parts = [f'<label class="tree-ver-wrap"><span class="visually-hidden">Version</span>'
             f'<select class="tree-ver-select" data-package="{_esc(full_name)}" aria-label="Version for {_esc(full_name)}">']
    for i, v in enumerate(versions):
        vc = v.get("channel", "lead")
        vhref = _url("channels", vc, "packs", full_name)
        sel = " selected" if (active_package == full_name and active_channel == vc) or (active_package != full_name and i == 0) else ""
        parts.append(f'<option value="{_esc(vc)}" data-href="{vhref}"{sel}>{_esc(v.get("label", vc))}</option>')
    parts.append("</select></label>")
    return "".join(parts)


def _role_svg(role: str) -> str:
    svg_body = {
        "host": '<rect x="1" y="2" width="14" height="9" rx="1" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M4 14h8M8 11v3" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>',
        "engine": '<path d="M8 1.5 13.5 4.5v7L8 14.5 2.5 11.5v-7L8 1.5z" fill="none" stroke="currentColor" stroke-width="1.4"/><circle cx="8" cy="8" r="2" fill="none" stroke="currentColor" stroke-width="1.4"/>',
        "kernel": '<circle cx="8" cy="8" r="5.5" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M8 5v3l2 2" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>',
        "arch": '<ellipse cx="8" cy="4" rx="5.5" ry="2" fill="none" stroke="currentColor" stroke-width="1.4"/><path d="M2.5 4v4c0 1.1 2.5 2 5.5 2s5.5-.9 5.5-2V4M2.5 8v4c0 1.1 2.5 2 5.5 2s5.5-.9 5.5-2V8" fill="none" stroke="currentColor" stroke-width="1.4"/>',
    }[role]
    title = {
        "host": "Unix host seat — curl-and-run; Inspect only; Play disabled",
        "engine": "CDN engine — Inspect only; Play disabled",
        "kernel": "Kernel module — Inspect only; Play disabled",
        "arch": "Architecture image — freestanding firmware; Inspect only; Play disabled",
    }[role]
    return (f'<span class="role-mark role-{role}" title="{title}" aria-label="{role}">'
            f'<svg class="role-icon" viewBox="0 0 16 16" width="12" height="12" aria-hidden="true">{svg_body}</svg></span>')


def _tree_node_li(node: dict, active_package: str, active_channel: str) -> str:
    cls = ["tree-node"]
    if node.get("is_folder"):
        cls.append("is-folder")
    elif node.get("is_package"):
        cls.append("is-leaf")
        cls.append("is-package")
    else:
        cls.append("is-leaf")
    if node.get("origin") == "remote":
        cls.append("is-remote")
    role = node.get("role")
    if role:
        cls.append(f"is-{role}")
    data = [
        f'data-name="{_esc((node.get("name") or "").lower())}"',
        f'data-origin="{_esc(node.get("origin") or "local")}"',
        f'data-role="{_esc(role or "")}"',
    ]
    if node.get("full_name"):
        data.append(f'data-package="{_esc(node["full_name"])}" data-search="{_esc(node["full_name"].lower())}"')
    else:
        data.append(f'data-search="{_esc((node.get("name") or "").lower())}"')
    out = [f'<li class="{" ".join(cls)} {" ".join(data)}">', _tree_row(node, active_package, active_channel)]
    kids = node.get("children") or []
    if kids:
        out.append('<ul class="tree tree-children" hidden>')
        for c in kids:
            out.append(_tree_node_li(c, active_package, active_channel))
        out.append("</ul>")
    out.append("</li>")
    return "".join(out)


def nav_html(nav_roots: list, active_package: str, active_channel: str) -> str:
    out = ['<ul class="tree tree-roots">']
    for n in nav_roots or []:
        out.append(_tree_node_li(n, active_package, active_channel))
    out.append("</ul>")
    return "".join(out)


def _shaped_repl_engines(raw) -> list:
    out = []
    for e in raw or []:
        out.append({
            "id": e.get("id", ""),
            "label": e.get("label") or e.get("id", ""),
            "title": e.get("title", e.get("id", "")),
            "ready": bool(e.get("ready")),
            "mjs_href": e.get("mjs_href") or "",
        })
    if not out:
        out.append({"id": "mp", "label": "mp", "title": "metal arch.wasm seat",
                    "ready": bool(raw), "mjs_href": ""})
    return out


def build_shell_ctx(ctx: dict, content: str, tree: str) -> dict:
    """Expand a page context into the full field set the shell template reads."""
    current_user = ctx.get("current_user")
    d = dict(ctx)
    d["content"] = content
    d["content_nav"] = tree
    d["app_version"] = __version__
    d["site_css"] = href("static", "site.css") + (f"?v={ctx.get('site_css_v')}" if ctx.get("site_css_v") else "")
    d["inspect_js"] = href("static", "inspect", "main.js") + (f"?v={ctx.get('inspect_js_v')}" if ctx.get("inspect_js_v") else "")
    d["page_head"] = ctx.get("page_head") or ""
    d["body_class"] = "page-docs" if ctx.get("active_page") == "docs" else ""
    d["base_path"] = ctx.get("base_path") or ""
    d["experimental"] = bool(ctx.get("experimental"))
    d["experimental_message"] = ctx.get("experimental_message") or (
        "Experimental CDN: data will be wiped — often. Short tests only; do not run weekend-long "
        "experiments against it. Not for production.")
    d["brand_name"] = ctx.get("brand_name") or "wasmmod-cdn"
    d["title"] = ctx.get("title") or d["brand_name"]
    d["home_href"] = href("channels", "lead")
    d["brand_logo"] = ctx.get("brand_logo_url") or href("static", "img", "pymergetic.png")
    d["health_href"] = href("health")
    d["main_class"] = ctx.get("main_class") or ""
    d["active_package"] = ctx.get("active_package") or ""
    d["current_user"] = current_user
    d["user_email"] = getattr(current_user, "email", "") if current_user else ""

    active_page = ctx.get("active_page") or ""
    def nav_cls(page):
        return "is-active" if (active_page == page or (page == "browse" and active_page in ("browse", ""))) else ""
    d["nav_browse"] = href("channels", "lead")
    d["nav_users"] = href("authors")
    d["nav_publish"] = href("publish")
    d["nav_sessions"] = href("sessions")
    d["nav_docs"] = href("docs")
    d["nav_login"] = href("login")
    d["nav_browse_cls"] = nav_cls("browse")
    d["nav_users_cls"] = nav_cls("users")
    d["nav_publish_cls"] = nav_cls("publish")
    d["nav_sessions_cls"] = nav_cls("sessions")
    d["nav_docs_cls"] = nav_cls("docs")
    d["nav_login_cls"] = nav_cls("login")
    is_admin = bool(current_user and getattr(current_user, "is_admin", False))
    d["nav_federation"] = href("federation") if is_admin else ""
    d["nav_federation_cls"] = nav_cls("federation")

    repl = bool(ctx.get("experimental_repl"))
    d["experimental_repl"] = repl
    d["repl_ready"] = bool(ctx.get("repl_ready"))
    d["repl_default_engine"] = ctx.get("repl_default_engine") or "mp"
    d["repl_asset_v"] = ctx.get("repl_asset_v") or ""
    repl_v = d["repl_asset_v"]
    repl_mjs = ctx.get("repl_default_mjs") or href("static", "repl", "micropython.mjs")
    d["repl_mjs_url"] = repl_mjs + (f"?v={repl_v}" if repl_v else "")
    d["repl_autoexec"] = href("repl", "autoexec.py")
    d["repl_js_src"] = href("static", "repl.js")
    d["cdn_base"] = ctx.get("base_path") or ""
    d["repl_engines"] = _shaped_repl_engines(ctx.get("repl_engines"))
    return d


def shape_nav_nodes(nodes) -> list[dict]:
    """Convert PackageNavNode/web ctx tree nodes to the plain dicts templates read."""
    out = []
    for node in nodes or []:
        if isinstance(node, dict):
            is_package = bool(node.get("full_name") or node.get("is_package"))
            is_folder = bool(node.get("children") or node.get("is_folder"))
            children = node.get("children") or []
        else:
            is_package = bool(getattr(node, "full_name", None))
            is_folder = bool(getattr(node, "children", None))
            children = getattr(node, "children", None) or []
            node = {
                "name": getattr(node, "name", ""),
                "full_name": getattr(node, "full_name", None),
                "origin": getattr(node, "origin", "local"),
                "peer_browse_url": getattr(node, "peer_browse_url", None),
                "role": getattr(node, "role", None),
            }
        versions = node.get("versions") or []
        if versions and not isinstance(versions[0], dict):
            versions = [
                {"channel": getattr(v, "channel", ""), "version": getattr(v, "version", ""),
                 "label": getattr(v, "label", getattr(v, "channel", ""))}
                for v in versions
            ]
        out.append({
            "name": node.get("name", ""),
            "full_name": node.get("full_name"),
            "origin": node.get("origin") or "local",
            "peer_browse_url": node.get("peer_browse_url"),
            "role": node.get("role"),
            "is_package": is_package,
            "is_folder": is_folder,
            "children": shape_nav_nodes(children),
            "versions": versions,
        })
    return out


def shape_catalog(catalog) -> list[dict]:
    """Convert PackageSummary rows to the plain dicts home.html reads."""
    out = []
    for pkg in catalog or []:
        if isinstance(pkg, dict):
            data = pkg
        else:
            data = {
                "name": getattr(pkg, "name", ""),
                "version": getattr(pkg, "version", ""),
                "channel": getattr(pkg, "channel", "lead"),
                "artifact_count": getattr(pkg, "artifact_count", 0),
                "maintainer_email": getattr(pkg, "maintainer_email", None),
                "description": getattr(pkg, "description", None),
                "yanked": getattr(pkg, "yanked", False),
                "deprecated": getattr(pkg, "deprecated", False),
                "updated_at": getattr(pkg, "updated_at", None),
                "version_count": getattr(pkg, "version_count", 1),
                "deps": getattr(pkg, "deps", None) or {},
                "deps_ok": getattr(pkg, "deps_ok", None) or {},
                "needed_by": getattr(pkg, "needed_by", None) or [],
                "origin": getattr(pkg, "origin", "local"),
                "peer_label": getattr(pkg, "peer_label", None),
                "peer_browse_url": getattr(pkg, "peer_browse_url", None),
                "role": getattr(pkg, "role", None),
            }
        row = dict(data)
        row.setdefault("version_count", 1)
        row.setdefault("artifact_count", 0)
        row["name_lower"] = _esc(str(row.get("name", "")).lower())
        row["href"] = _url(*_channel_path_and_pack(row.get("channel", "lead"), str(row.get("name", ""))))
        row["origin"] = row.get("origin") or "local"
        row["peer_label"] = row.get("peer_label") or "remote CDN"
        row["peer_browse_url"] = row.get("peer_browse_url") or ""
        data_search = " ".join((str(row.get("name", "")), str(row.get("maintainer_email") or ""),
                                str(row.get("description") or ""),
                                " ".join(row.get("deps", {}).keys()),
                                " ".join(row.get("needed_by", [])),
                                str(row.get("peer_label") or ""), str(row.get("origin") or ""))).lower()
        row["data_search"] = _esc(data_search)
        row["data_yanked"] = "1" if row.get("yanked") else "0"
        row["data_deprecated"] = "1" if row.get("deprecated") else "0"
        updated = row.get("updated_at")
        row["data_updated"] = _esc(updated.isoformat()) if updated else ""
        row["updated_str"] = updated.strftime("%Y-%m-%d %H:%M") if updated else ""
        row["deps_len"] = len(row.get("deps", {}))
        row["needed_len"] = len(row.get("needed_by", []))
        row["lead_pill"] = " pill-lead" if row.get("channel") == "lead" else ""
        dep_links = []
        for i, (dep, ver) in enumerate(row.get("deps", {}).items()):
            ok = bool(row.get("deps_ok", {}).get(dep, False))
            dep_links.append({
                "name": _esc(dep),
                "cls": "dep-chip-ok" if ok else "dep-chip-bad",
                "href": _url(*_channel_path_and_pack("lead", str(dep))),
                "title": _esc(f"{dep}@{ver} available" if ok else f"{dep}@{ver} missing or yanked"),
                "last": i == len(row["deps"]) - 1,
            })
        row["dep_links"] = dep_links
        needed = row.get("needed_by", [])
        multi = len(needed) > 1
        needed_links = [
            {"name": _esc(dep), "cls": " dep-chip-rev" if multi else "",
             "href": _url(*_channel_path_and_pack("lead", str(dep))), "last": i == len(needed) - 1}
            for i, dep in enumerate(needed)
        ]
        row["needed_links"] = needed_links
        out.append(row)
    return out


def _human_size(n) -> str:
    try:
        bytes_ = abs(int(n))
    except (TypeError, ValueError):
        return "?"
    if bytes_ < 1024:
        return "%d B" % bytes_
    units = ("KiB", "MiB", "GiB", "TiB")
    value = float(bytes_)
    unit = units[0]
    for u in units:
        value /= 1024.0
        unit = u
        if value < 1024:
            break
    if value >= 100:
        pretty = "%.0f" % value
    elif value >= 10:
        pretty = "%.1f" % value
    else:
        pretty = "%.2f" % value
    return "%s %s (%d B)" % (pretty, unit, bytes_)


def shape_package(ctx: dict) -> dict:
    """Expand a package-page ctx into the plain dicts package.html reads."""
    entry = ctx.get("entry")
    if isinstance(entry, dict):
        e = dict(entry)
    else:
        e = {
            "version": getattr(entry, "version", ""),
            "aot_version": getattr(entry, "aot_version", None),
            "deps": getattr(entry, "deps", None) or {},
            "artifacts": getattr(entry, "artifacts", None) or [],
            "maintainer_email": getattr(entry, "maintainer_email", None),
            "description": getattr(entry, "description", None),
            "homepage": getattr(entry, "homepage", None),
            "license": getattr(entry, "license", None),
            "yanked": getattr(entry, "yanked", False),
            "yank_reason": getattr(entry, "yank_reason", None),
            "deprecated": getattr(entry, "deprecated", False),
            "successor": getattr(entry, "successor", None),
            "contents": getattr(entry, "contents", None),
        }
    d = dict(ctx)
    d["name"] = ctx.get("name", "")
    d["channel"] = ctx.get("channel", "lead")
    d["entry"] = e
    d["package_role"] = ctx.get("package_role")
    d["cdn_base"] = ctx.get("cdn_base") or ""
    d["is_unix_seat"] = bool(ctx.get("is_unix_seat"))
    d["is_arch_seat"] = bool(ctx.get("is_arch_seat"))
    d["fed_origin"] = ctx.get("fed_origin", "local")
    d["fed_peer_label"] = ctx.get("fed_peer_label") or ""
    d["fed_peer_browse_url"] = ctx.get("fed_peer_browse_url") or ""
    d["needed_by_len"] = len(ctx.get("needed_by") or [])

    # deps
    deps_ok = ctx.get("deps_ok") or {}
    d["deps_links"] = []
    for dep, ver in e.get("deps", {}).items():
        ok = bool(deps_ok.get(dep, False))
        d["deps_links"].append({
            "name": dep,
            "ok": ok,
            "href": _url(*_channel_path_and_pack("lead", str(dep))),
            "ver_href": _url(*_channel_path_and_pack("@%s" % ver, str(dep))),
            "ver": ver,
        })

    needed_by = ctx.get("needed_by") or []
    d["needed_links"] = [
        {"name": dep, "cls": " dep-chip-rev" if len(needed_by) > 1 else "",
         "href": _url(*_channel_path_and_pack("lead", str(dep)))}
        for dep in needed_by
    ]

    # version history
    versions = ctx.get("package_versions") or []
    d["package_versions"] = []
    for i, v in enumerate(versions):
        vc = v.channel if not isinstance(v, dict) else v.get("channel", "lead")
        vver = v.version if not isinstance(v, dict) else v.get("version", "")
        varts = v.artifact_count if not isinstance(v, dict) else v.get("artifact_count", 0)
        vlabel = v.label if not isinstance(v, dict) else v.get("label", vc)
        d["package_versions"].append({
            "channel": vc, "version": vver, "artifact_count": varts, "label": vlabel,
            "href": _url(*_channel_path_and_pack(vc, str(d["name"]))),
            "is_current": vc == d["channel"],
            "folded": i >= 3, "index": i,
        })
    d["package_versions_len"] = len(d["package_versions"])
    d["version_fold_count"] = len(d["package_versions"]) - 3 if len(d["package_versions"]) > 3 else 0

    # artifacts
    channel = d["channel"]
    name = d["name"]
    arts = []
    for a in e.get("artifacts", []):
        path = a.path if not isinstance(a, dict) else a.get("path", "")
        kind = (
            (a.kind.value if not isinstance(a.kind, str) else a.kind)
            if not isinstance(a, dict) and a.kind
            else (a.get("kind") if isinstance(a, dict) else "")
        )
        encoding = (
            (a.encoding.value if not isinstance(a.encoding, str) else a.encoding)
            if not isinstance(a, dict)
            else (a.get("encoding") or "")
        )
        size = a.size if not isinstance(a, dict) else a.get("size", 0)
        arch = (getattr(a, "arch", None) if not isinstance(a, dict) else a.get("arch"))
        if channel == "lead":
            seg = ("artifacts", "lead", path)
        else:
            seg = ("artifacts", "pin", channel.lstrip("@"), path)
        arts.append({
            "path": path, "kind": kind, "encoding": encoding, "size": size, "arch": arch,
            "version_arg": d.get("version_arg", ""),
            "size_str": _human_size(size),
            "dl_href": _url(*seg),
            "inspect_href": _url(*seg, "inspect"),
            "files_base": _url(*seg, "files"),
            "files_raw": _url(*seg, "files", "raw"),
            "sections_raw": _url(*seg, "sections", "raw"),
            "encoding_raw": encoding == "raw",
            "ends_elf": path.endswith(".elf"),
            "ends_efi": path.endswith(".efi"),
            "ends_efi_zlib": path.endswith(".efi.zlib"),
            "ends_elf_zlib": path.endswith(".elf.zlib"),
            "is_wasm": path.endswith(".wasm") or path.endswith(".wasm.zlib") or ".aot" in path,
        })
    d["entry"]["artifacts"] = arts
    d["artifacts_len"] = len(arts)
    d["has_elf"] = any(a["ends_elf"] for a in arts)
    d["has_efi"] = any(a["ends_efi"] for a in arts)
    d["run_artifacts"] = [a for a in arts if a["encoding_raw"] and a["ends_elf"]]
    d["ipxe_artifacts"] = [a for a in arts if a["encoding_raw"] and (a["ends_elf"] or a["ends_efi"])]

    contents = e.get("contents")
    if contents is not None and not isinstance(contents, dict):
        contents = {
            "name": getattr(contents, "name", None),
            "pkg_version": getattr(contents, "pkg_version", None),
            "signed": getattr(contents, "signed", False),
            "has_pack": getattr(contents, "has_pack", False),
            "has_source": getattr(contents, "has_source", False),
        }
    d["entry"]["contents"] = contents

    d["no_play_role"] = d["package_role"] in ("host", "kernel", "arch", "engine")
    d["show_try"] = bool(ctx.get("experimental_repl")) and bool(ctx.get("repl_ready")) and not d["no_play_role"]

    d["channel_href"] = ctx.get("channel_href") or _url(*_channel_path_and_pack(str(d["channel"]), str(d["name"])))
    d["author_href"] = ctx.get("author_href")
    d["version_arg"] = str(d["channel"]).lstrip("@") if d["channel"] != "lead" else ""
    _VENDOR = [
        "highlight.min.js", "lang-python.min.js", "lang-c.min.js", "lang-cpp.min.js",
        "lang-rust.min.js", "lang-javascript.min.js", "lang-typescript.min.js",
        "lang-json.min.js", "lang-markdown.min.js", "lang-ini.min.js", "lang-yaml.min.js",
        "lang-xml.min.js", "lang-bash.min.js", "lang-makefile.min.js", "lang-cmake.min.js",
        "lang-diff.min.js", "lang-llvm.min.js", "lang-go.min.js", "lang-java.min.js",
        "lang-sql.min.js", "lang-ruby.min.js", "lang-perl.min.js", "lang-dockerfile.min.js",
        "lang-lisp.min.js", "lang-x86asm.min.js",
    ]
    for i, v in enumerate(_VENDOR):
        d["vendor_%d" % i] = _url("static", "vendor", v)
    return d


def _channel_path_and_pack(channel: str, name: str):
    from pymergetic.wasmmod.cdn.paths import package_path
    p = package_path(channel, name)
    return tuple(part.strip("/") for part in p.split("/") if part and part.strip("/"))


def shape_author_packages(packages) -> list[dict]:
    out = []
    for p in packages or []:
        if isinstance(p, dict):
            out.append({
                "name": p.get("name", ""),
                "version": p.get("version", ""),
                "description": p.get("description") or "",
                "artifact_count": p.get("artifact_count", 0),
                "href": _url(*_channel_path_and_pack(p.get("channel", "lead"), str(p.get("name", "")))),
            })
        else:
            out.append({
                "name": getattr(p, "name", ""),
                "version": getattr(p, "version", ""),
                "description": getattr(p, "description", "") or "",
                "artifact_count": getattr(p, "artifact_count", 0),
                "href": _url(*_channel_path_and_pack(getattr(p, "channel", "lead"), str(getattr(p, "name", "")))),
            })
    return out


def shape_maintainers(maintainers) -> list[dict]:
    out = []
    for m in maintainers or []:
        email = m.email if not isinstance(m, dict) else m.get("email", "")
        out.append({
            "email": email,
            "package_count": m.package_count if not isinstance(m, dict) else m.get("package_count", 0),
            "href": _url("authors", email),
        })
    return out


def shape_sessions(rows, activities) -> list[dict]:
    out = []
    for s in rows or []:
        act = (activities or {}).get(str(getattr(s, "id", "")))
        recent = []
        if act is not None:
            raw = act.recent if not isinstance(act, dict) else act.get("recent") or []
            recent = raw[:8]
        buckets = []
        peak = 1
        if act is not None:
            counts = [b.count for b in (act.buckets if not isinstance(act, dict) else act.get("buckets") or [])]
            peak = max(counts) if counts else 1
            if peak < 1:
                peak = 1
            for b in (act.buckets if not isinstance(act, dict) else act.get("buckets") or []):
                count = b.count if not isinstance(b, dict) else b.get("count", 0)
                minute = b.minute if not isinstance(b, dict) else b.get("minute", "")
                buckets.append({"count": count, "minute": minute,
                                "height": round((count / peak) * 100, 1) if count else 0})
        last_kind = ""
        last_package = ""
        if recent:
            r0 = recent[0]
            last_kind = r0.kind if not isinstance(r0, dict) else r0.get("kind", "")
            last_package = r0.package if (not isinstance(r0, dict)) and r0.package else (r0.get("package", "") if isinstance(r0, dict) else "")
        recent_shaped = []
        for ev in recent:
            if isinstance(ev, dict):
                recent_shaped.append({"kind": ev.get("kind", ""), "package": ev.get("package") or "",
                                      "path": ev.get("path") or ""})
            else:
                recent_shaped.append({"kind": ev.kind, "package": getattr(ev, "package", None) or "",
                                      "path": getattr(ev, "path", None) or ""})
        out.append({
            "full_id": str(getattr(s, "id", "")),
            "id_short": str(getattr(s, "id", ""))[:8],
            "principal_label": getattr(s, "principal_label", ""),
            "last_activity_at": getattr(s, "last_activity_at", "") if not isinstance(
                s, dict) else s.get("last_activity_at", ""),
            "base_disp": (getattr(s, "cdn_base", None) or "—") if not isinstance(
                s, dict) else (s.get("cdn_base") or "—"),
            "channel": getattr(s, "channel", "") if not isinstance(s, dict) else s.get("channel", ""),
            "driver_disp": (getattr(s, "driver", None) or "—") + (
                " · hook on" if getattr(s, "hook_on", False) else "") if not isinstance(
                s, dict) else (s.get("driver") or "—"),
            "spark": buckets,
            "window_minutes": act.window_minutes if (act is not None and not isinstance(act, dict)) else (
                act.get("window_minutes", 0) if isinstance(act, dict) else 0),
            "recent_len": len(recent),
            "last_kind": last_kind,
            "last_package": last_package,
            "recent": recent_shaped,
        })
    return out


def _to_attr(value):
    """Recursively convert dicts to attr-access objects (utemplate emits `.x`).

    utemplate compiles ``{{ d.field }}`` verbatim as ``d.field``, so the context
    (and every nested dict/lookup) must be attribute-addressable, not plain
    ``dict``. ``AttrDict`` also keeps ``["k"]`` and ``.get()`` working for the
    handful of Python-side helpers that read context.
    """
    if isinstance(value, dict):
        return AttrDict({k: _to_attr(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return [_to_attr(v) for v in value]
    if isinstance(value, set):
        return [_to_attr(v) for v in value]
    return value


class AttrDict(dict):
    """dict that also exposes keys as attributes (for utemplate's ``.attr``)."""

    def __getattr__(self, item):
        try:
            return self[item]
        except KeyError:
            raise AttributeError(item) from None


def _render_ctx(ctx: dict, name: str) -> str:
    """Render ``name`` against ``ctx`` where nested dicts are attr-addressable."""
    return render_raw(name, _to_attr(ctx))


def render_page(name: str, ctx: dict, nav: str | None = None) -> str:
    """Render a full page: body template embedded in the shared shell template."""
    # Shape raw ORM/registry context into the plain dicts templates can read.
    body_ctx = dict(ctx)
    body_ctx["catalog"] = shape_catalog(ctx.get("catalog"))
    body_ctx["catalog_len"] = len(body_ctx["catalog"])
    body_ctx["nav_roots"] = shape_nav_nodes(ctx.get("nav_roots", []))
    body_ctx["active_channel"] = ctx.get("active_channel", "lead")
    # Build the full shell context first so body templates see shared fields
    # (base_path, experimental, current_user, brand, ...) even before content.
    pre = build_shell_ctx(body_ctx, "", "")
    body_html = _render_ctx(pre, name)
    if nav is None:
        nav = nav_html(body_ctx["nav_roots"], ctx.get("active_package"), ctx.get("active_channel", "lead"))
    d = build_shell_ctx(body_ctx, body_html, nav or "<p class=\"tree-empty\">No packages yet</p>")
    return _render_ctx(d, "shell.html")


configure_web("/")
