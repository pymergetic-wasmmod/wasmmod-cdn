"""Shell session context for browse pages + autoexec."""
from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import Request

from pymergetic.metal.cdn.api.deps import SESSION_USER_KEY
from pymergetic.metal.cdn.db import Database
from pymergetic.metal.cdn.models import UserRead
from pymergetic.metal.cdn.services.channel import IndexService
from pymergetic.metal.cdn.services.identity import UserService
from pymergetic.metal.cdn.web.context import _url, resolve_brand_logo_url


async def _session_user(request: Request) -> UserRead | None:
    raw = request.session.get(SESSION_USER_KEY)
    if not raw:
        return None
    try:
        user_id = UUID(str(raw))
    except ValueError:
        return None
    db: Database | None = getattr(request.app.state, "db", None)
    if db is None:
        return None
    async for session in db.session():
        user = await UserService(session).get(user_id)
        if user is not None and user.is_active:
            return user
        return None
    return None


async def _shell_context(
    indexes: IndexService,
    *,
    active_channel: str,
    active_package: str | None = None,
    page: str = "browse",
    request: Request | None = None,
    current_user: UserRead | None = None,
) -> dict[str, Any]:
    catalog = await indexes.list_catalog(include_yanked=False)
    nav_roots = await indexes.browse_package_nav()
    package_versions: list = []
    if active_package:
        package_versions = await indexes.package_versions(active_package)
    experimental = False
    experimental_message: str | None = None
    experimental_repl = False
    repl_ready = False
    repl_asset_v = ""
    repl_engines: list[dict[str, Any]] = []
    repl_default_engine = "mp"
    repl_default_mjs = ""
    site_css_v = ""
    inspect_js_v = ""
    bootstrap_admin_email: str | None = None
    brand_name = "metal-cdn"
    brand_logo_url = _url("static", "img", "pymergetic.png")
    public_origin: str | None = None
    settings = None
    try:
        static_dir = Path(__file__).resolve().parent / "static"
        css = static_dir / "site.css"
        mtimes_css: list[int] = []
        if css.is_file():
            mtimes_css.append(int(css.stat().st_mtime))
        css_dir = static_dir / "css"
        if css_dir.is_dir():
            for child in css_dir.glob("*.css"):
                try:
                    mtimes_css.append(int(child.stat().st_mtime))
                except OSError:
                    pass
        fed_js = static_dir / "federation.js"
        if fed_js.is_file():
            try:
                mtimes_css.append(int(fed_js.stat().st_mtime))
            except OSError:
                pass
        if mtimes_css:
            site_css_v = format(max(mtimes_css), "x")
        inspect_js = static_dir / "inspect.js"
        inspect_main = static_dir / "inspect" / "main.js"
        mtimes: list[int] = []
        if inspect_js.is_file():
            mtimes.append(int(inspect_js.stat().st_mtime))
        if inspect_main.is_file():
            mtimes.append(int(inspect_main.stat().st_mtime))
        inspect_dir = static_dir / "inspect"
        if inspect_dir.is_dir():
            for child in inspect_dir.glob("*.js"):
                try:
                    mtimes.append(int(child.stat().st_mtime))
                except OSError:
                    pass
        if mtimes:
            inspect_js_v = format(max(mtimes), "x")
    except OSError:
        site_css_v = ""
        inspect_js_v = ""
    if request is not None:
        settings = getattr(request.app.state, "settings", None)
        if settings is not None and getattr(settings, "experimental", False):
            experimental = True
            experimental_message = getattr(settings, "experimental_message", None)
        if settings is not None and getattr(settings, "experimental_repl", False):
            experimental_repl = True
            repl_dir = Path(__file__).resolve().parent / "static" / "repl"
            # mp = CDN lead pymergetic.metal.arch.wasm (static/repl/mp fallback).
            # mpwm / upy stay local static engines.
            ARCH_WASM_PKG = "pymergetic.metal.arch.wasm"
            arch_mjs_href = ""
            try:
                arch_entry = await indexes.get_package(
                    IndexService.parse_channel("lead"), ARCH_WASM_PKG
                )
            except Exception:
                arch_entry = None
            if arch_entry is not None and not arch_entry.yanked:
                for art in arch_entry.artifacts:
                    path = art.path
                    if path.endswith(".mjs") and not path.endswith(".zlib"):
                        arch_mjs_href = _url("artifacts", "lead", path)
                        break

            engine_specs = (
                ("mp", "mp", "metal arch.wasm seat (CDN lead)"),
                ("mpwm", "mpwm", "wasmmod host only (no metal arch)"),
                ("upy", "upy", "vanilla upstream MicroPython"),
            )
            all_mtimes: list[int] = []
            for eng_id, subdir, label in engine_specs:
                eng_dir = repl_dir / subdir
                mjs = eng_dir / "micropython.mjs"
                wasm = eng_dir / "micropython.wasm"
                # Accept former mp-wm/ dir during transition.
                if eng_id == "mpwm" and not mjs.is_file():
                    legacy = repl_dir / "mp-wm"
                    if (legacy / "micropython.mjs").is_file():
                        eng_dir = legacy
                        mjs = eng_dir / "micropython.mjs"
                        wasm = eng_dir / "micropython.wasm"
                        subdir = "mp-wm"
                    else:
                        mjs = repl_dir / "micropython.mjs"
                        wasm = repl_dir / "micropython.wasm"
                mtimes: list[int] = []
                try:
                    if mjs.is_file():
                        mtimes.append(int(mjs.stat().st_mtime))
                    if wasm.is_file():
                        mtimes.append(int(wasm.stat().st_mtime))
                except OSError:
                    mtimes = []
                all_mtimes.extend(mtimes)

                mjs_href = ""
                if eng_id == "mp" and arch_mjs_href:
                    mjs_href = arch_mjs_href
                    ready = True
                elif mjs.is_file():
                    ready = True
                    if (eng_dir / "micropython.mjs").is_file() and eng_dir != repl_dir:
                        mjs_parts = ("static", "repl", subdir, "micropython.mjs")
                    else:
                        mjs_parts = ("static", "repl", "micropython.mjs")
                    mjs_href = _url(*mjs_parts)
                else:
                    ready = False

                repl_engines.append(
                    {
                        "id": eng_id,
                        "label": eng_id,
                        "title": label,
                        "ready": ready,
                        "mjs_href": mjs_href,
                    }
                )
            ready_ids = [e["id"] for e in repl_engines if e["ready"]]
            repl_ready = bool(ready_ids)
            for pref in ("mp", "mpwm", "upy"):
                if pref in ready_ids:
                    repl_default_engine = pref
                    break
            else:
                if ready_ids:
                    repl_default_engine = ready_ids[0]
            for e in repl_engines:
                if e["id"] == repl_default_engine and e["ready"]:
                    repl_default_mjs = e["mjs_href"]
                    break
            if not repl_default_mjs:
                repl_default_mjs = _url("static", "repl", "micropython.mjs")
            try:
                if all_mtimes:
                    repl_asset_v = format(max(all_mtimes), "x")
            except OSError:
                repl_asset_v = ""
        if settings is not None and getattr(settings, "bootstrap_admin_email", None):
            bootstrap_admin_email = str(settings.bootstrap_admin_email)
        if settings is not None:
            brand_name = getattr(settings, "display_brand_name", None) or brand_name
            brand_logo_url = resolve_brand_logo_url(
                settings, default_href=_url("static", "img", "pymergetic.png")
            )
            public_origin = getattr(settings, "public_origin", None)
        if current_user is None:
            current_user = await _session_user(request)
        # Federated catalog / nav (soft-fail).
        try:
            from pymergetic.metal.cdn.web.page_helpers import _merge_fed_catalog

            catalog, nav_roots = await _merge_fed_catalog(request, catalog, nav_roots)
            if active_package and not package_versions:
                for row in catalog:
                    if row.name == active_package:
                        package_versions = [
                            {
                                "channel": row.channel,
                                "version": row.version,
                                "label": (
                                    f"lead ({row.version})"
                                    if row.channel == "lead"
                                    else f"{row.channel.lstrip('@')} ({row.version})"
                                ),
                                "artifact_count": row.artifact_count,
                            }
                        ]
                        break
        except Exception:
            pass
    return {
        "catalog": catalog,
        "nav_roots": nav_roots,
        "package_versions": package_versions,
        "active_channel": active_channel,
        "active_package": active_package,
        "active_page": page,
        "experimental": experimental,
        "experimental_message": experimental_message,
        "experimental_repl": experimental_repl,
        "repl_ready": repl_ready,
        "repl_asset_v": repl_asset_v,
        "repl_engines": repl_engines,
        "repl_default_engine": repl_default_engine,
        "repl_default_mjs": repl_default_mjs,
        "site_css_v": site_css_v,
        "inspect_js_v": inspect_js_v,
        "bootstrap_admin_email": bootstrap_admin_email,
        "current_user": current_user,
        "brand_name": brand_name,
        "brand_logo_url": brand_logo_url,
        "public_origin": public_origin,
    }


