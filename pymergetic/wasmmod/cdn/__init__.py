"""pymergetic.wasmmod.cdn — async FastAPI CDN for wasmmod packs."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version


def resolve_version() -> str:
    try:
        from pymergetic.wasmmod.cdn._version import version as scm_version

        return scm_version
    except ImportError:
        pass
    try:
        return version("pymergetic-wasmmod-cdn")
    except PackageNotFoundError:
        return _git_describe_fallback()


def _git_describe_fallback() -> str:
    """Last resort when not installed editable and _version.py is absent."""
    import subprocess
    from pathlib import Path

    # pymergetic/wasmmod/cdn/__init__.py → repo root
    root = Path(__file__).resolve().parents[3]
    try:
        out = subprocess.run(
            ["git", "describe", "--tags", "--always", "--dirty", "--long"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
        desc = out.stdout.strip()
        return desc if desc else "0.0.0+unknown"
    except (OSError, subprocess.CalledProcessError):
        return "0.0.0+unknown"


__version__ = resolve_version()
