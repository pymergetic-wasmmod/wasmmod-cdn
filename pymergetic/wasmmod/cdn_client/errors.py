"""User-facing CLI error formatting for wasmmod-cdn / wasmmod tools.

Keep messages short, on stderr via :func:`report`, with optional indented hints.
Set ``WASMMOD_CDN_DEBUG=1`` (or ``WASMMOD_DEBUG=1``) to re-raise unexpected errors
with a full traceback from :func:`invoke`.
"""

from __future__ import annotations

import os
import sys
import traceback
from typing import NoReturn

from pymergetic.wasmmod.cdn_client.client import ClientError


def hints_for_client_error(exc: ClientError, *, force: bool = False) -> list[str]:
    """Contextual follow-ups for common CDN HTTP / config failures."""
    msg = str(exc).lower()
    status = exc.status
    hints: list[str] = []

    if status == 409 and not force:
        if "force" in msg or "immutable" in msg or "already" in msg:
            hints.append("Re-run with --force to overwrite an existing pin/version")
    elif status in (401, 403):
        hints.append("Check credentials: wasmmod-cdn login --url …  (or --token / WASMMOD_CDN_TOKEN)")
        hints.append("Verify with: wasmmod-cdn whoami")
    elif status == 404:
        hints.append("Check package/channel name, or publish first")
    elif status == 429:
        hints.append("Rate limited — wait and retry")
    elif status is None and "connection failed" in msg:
        hints.append("Is the CDN up? (e.g. wasmmod-cdn serve)")
        hints.append("Check --cdn-url / WASMMOD_CDN_URL / wasmmod-cdn login config")
    elif status is None and ("not set" in msg or "not logged in" in msg):
        hints.append("wasmmod-cdn login --url http://127.0.0.1:8000/cdn --email … --register")

    return hints


def format_error(prog: str, message: str, *hints: str) -> str:
    """``prog: message`` plus indented hint lines."""
    lines = [f"{prog}: {message}"]
    for hint in hints:
        if hint:
            lines.append(f"  {hint}")
    return "\n".join(lines)


def format_client_error(
    exc: ClientError,
    *,
    prog: str = "wasmmod-cdn",
    force: bool = False,
    extra_hints: list[str] | None = None,
) -> str:
    hints = hints_for_client_error(exc, force=force)
    if extra_hints:
        hints.extend(extra_hints)
    return format_error(prog, str(exc), *hints)


def exit_code_for(exc: BaseException) -> int:
    if isinstance(exc, ClientError):
        if exc.status in (401, 403):
            return 1
        if exc.status == 404:
            return 1
        if exc.status == 409:
            return 1
        if exc.status == 429:
            return 1
        return 1
    return 1


def report(
    prog: str,
    message: str,
    *hints: str,
    file=None,
) -> None:
    """Print a formatted error to stderr (does not exit)."""
    print(format_error(prog, message, *hints), file=file or sys.stderr)


def report_client_error(
    exc: ClientError,
    *,
    prog: str = "wasmmod-cdn",
    force: bool = False,
    extra_hints: list[str] | None = None,
    file=None,
) -> int:
    """Print ClientError + hints; return process exit code."""
    print(
        format_client_error(exc, prog=prog, force=force, extra_hints=extra_hints),
        file=file or sys.stderr,
    )
    return exit_code_for(exc)


def die(prog: str, message: str, *hints: str) -> NoReturn:
    """Raise SystemExit with a formatted message (printed by the interpreter / invoke)."""
    raise SystemExit(format_error(prog, message, *hints))


def die_client(
    exc: ClientError,
    *,
    prog: str = "wasmmod-cdn",
    force: bool = False,
    extra_hints: list[str] | None = None,
) -> NoReturn:
    raise SystemExit(
        format_client_error(exc, prog=prog, force=force, extra_hints=extra_hints)
    )


def _debug_enabled() -> bool:
    for key in ("WASMMOD_CDN_DEBUG", "WASMMOD_DEBUG"):
        val = os.environ.get(key, "").strip().lower()
        if val in ("1", "true", "yes", "on"):
            return True
    return False


def invoke(main_fn, *, prog: str = "wasmmod-cdn") -> int:
    """Run a CLI ``main()`` with clean handling of ClientError / unexpected errors."""
    try:
        return int(main_fn())
    except SystemExit as exc:
        code = exc.code
        if code is None:
            return 0
        if isinstance(code, int):
            return code
        # SystemExit("message") from die() / legacy raise SystemExit(str)
        if code:
            print(str(code), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(format_error(prog, "interrupted"), file=sys.stderr)
        return 130
    except ClientError as exc:
        return report_client_error(exc, prog=prog)
    except Exception as exc:
        if _debug_enabled():
            traceback.print_exc()
            return 1
        print(
            format_error(
                prog,
                f"unexpected error: {exc}",
                "Set WASMMOD_CDN_DEBUG=1 or WASMMOD_DEBUG=1 for a traceback",
            ),
            file=sys.stderr,
        )
        return 1
