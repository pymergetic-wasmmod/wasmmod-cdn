"""Generate the browser REPL ``autoexec.py`` session bootstrap script."""

from __future__ import annotations

import json
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path

_WEB = Path(__file__).resolve().parent
_TEMPLATE_PATH = _WEB / "autoexec.tpl.py"
_METAL_WASM_VENDORED = _WEB / "autoexec_metal_wasm.tpl.py"
# Sibling checkout: packages/metalpython/...
_METAL_WASM_LIVE = (
    _WEB.parents[4].parent
    / "metalpython"
    / "extmod"
    / "metal"
    / "src"
    / "pymergetic"
    / "metal"
    / "arch"
    / "wasm"
    / "autoexec.py"
)


@lru_cache(maxsize=1)
def _cdn_shell_template() -> str:
    return _TEMPLATE_PATH.read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def _metal_wasm_template() -> str | None:
    for p in (_METAL_WASM_LIVE, _METAL_WASM_VENDORED):
        try:
            if p.is_file():
                return p.read_text(encoding="utf-8")
        except OSError:
            continue
    return None


def _apply_sentinels(
    out: str,
    *,
    cdn_base: str,
    app_version: str,
    packages: Sequence[str],
    channel: str,
    session_id: str,
    principal: str,
    driver: str,
) -> str:
    base = cdn_base.rstrip("/")
    for sentinel, value in (
        ("__TPL_CDN__", json.dumps(base)),
        ("__TPL_CHANNEL__", json.dumps(channel)),
        ("__TPL_INDEX_URL__", json.dumps(f"{base}/index/{channel}")),
        ("__TPL_BROWSE_URL__", json.dumps(f"{base}/channels/{channel}")),
        ("__TPL_SERVER_VERSION__", json.dumps(app_version)),
        ("__TPL_SESSION_ID__", json.dumps(session_id)),
        ("__TPL_PRINCIPAL__", json.dumps(principal)),
        ("__TPL_DRIVER_HINT__", json.dumps(driver)),
        ("__TPL_LEAD_PACKAGES__", json.dumps(list(packages), indent=2)),
    ):
        out = out.replace(f'"{sentinel}"', value)
    return out


def render_autoexec(
    *,
    cdn_base: str,
    app_version: str,
    packages: Sequence[str],
    channel: str = "lead",
    session_id: str = "",
    principal: str = "anon",
    driver: str = "wasmmod-cdn",
    engine: str = "mpwm",
) -> str:
    """Return Python source for a fresh REPL session.

    ``engine=mp`` → metal post-ready CDN autoexec (boot tree is C in the seat).
    ``mpwm`` / ``upy`` → classic CDN shell template.
    """
    eng = (engine or "mpwm").strip().lower()
    if eng == "mp":
        metal = _metal_wasm_template()
        if metal is not None:
            body = _apply_sentinels(
                metal,
                cdn_base=cdn_base,
                app_version=app_version,
                packages=packages,
                channel=channel,
                session_id=session_id,
                principal=principal,
                driver=driver,
            )
            return body + "\n\n# wasmmod-cdn: post-ready autoexec (CDN hook)\nrun()\n"
    return _apply_sentinels(
        _cdn_shell_template(),
        cdn_base=cdn_base,
        app_version=app_version,
        packages=packages,
        channel=channel,
        session_id=session_id,
        principal=principal,
        driver=driver,
    )
