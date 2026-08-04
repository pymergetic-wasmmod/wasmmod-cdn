"""Generate the browser REPL ``autoexec.py`` session bootstrap script."""

from __future__ import annotations

import json
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path

_TEMPLATE_PATH = Path(__file__).with_name("autoexec.tpl.py")


@lru_cache(maxsize=1)
def _template() -> str:
    return _TEMPLATE_PATH.read_text(encoding="utf-8")


def render_autoexec(
    *,
    cdn_base: str,
    app_version: str,
    packages: Sequence[str],
    channel: str = "lead",
    session_id: str = "",
    principal: str = "anon",
    driver: str = "metal-cdn",
) -> str:
    """Return Python source for a fresh REPL session (no pack import).

    Sets ``wasm.cdn`` + ``install_hook``, prints a short intro, and defines
    ``packages()`` / ``help()`` helpers. ``cdn_base`` is an absolute CDN root
    (e.g. ``http://127.0.0.1:8000/cdn``).

    Source template: ``autoexec.tpl.py`` (``"__TPL_*__"`` sentinels → literals).
    """
    base = cdn_base.rstrip("/")
    # Replace quoted sentinels with JSON/Python literals (incl. quotes or [lists]).
    out = _template()
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
