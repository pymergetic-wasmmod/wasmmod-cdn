"""Argparse setup for wasmmod-cdn CLI."""

from __future__ import annotations

import argparse
from pathlib import Path

from pymergetic.wasmmod.cdn import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wasmmod-cdn", description="wasmmod-cdn CDN server & client")

    parser.add_argument("--version", action="version", version=__version__)

    sub = parser.add_subparsers(dest="cmd", required=True)

    serve = sub.add_parser("serve", help="Run the async FastAPI app")

    serve.add_argument("--host", default=None)

    serve.add_argument("--port", type=int, default=None)

    serve.add_argument("--reload", action="store_true")

    login = sub.add_parser("login", help="Store CDN URL + API key (password login or --token)")

    login.add_argument("--url", default=None, help="CDN base URL (e.g. http://127.0.0.1:8000/cdn)")

    login.add_argument("--email", default=None)

    login.add_argument("--password", default=None)

    login.add_argument(
        "--token",
        default=None,
        help="Existing API key (skip password; for CI). Implies --url is required.",
    )

    login.add_argument("--name", default="cli", help="API key label")

    login.add_argument(
        "--register",
        action="store_true",
        help="Register a new account before creating the key",
    )

    sub.add_parser("logout", help="Forget stored API key / URL")

    sub.add_parser("whoami", help="Show the authenticated user")

    claim = sub.add_parser("claim", help="Claim a package name")

    claim.add_argument("package")

    publish = sub.add_parser("publish", help="Upload pack artifacts to lead and/or pin")

    publish.add_argument("package")

    publish.add_argument("version")

    publish.add_argument("files", nargs="+", type=Path, help="Artifact files to upload")

    publish.add_argument("--no-lead", action="store_true")

    publish.add_argument("--no-pin", action="store_true")

    publish.add_argument("--aot-version", type=int, default=None)

    publish.add_argument("--claim", action="store_true", help="Claim package before publish")

    publish.add_argument(
        "--upstream",
        action="store_true",
        help="Publish via a PUSH federation mount on the CDN (form upstream=true)",
    )

    publish.add_argument(
        "--also-local",
        action="store_true",
        help="With --upstream, also write locally (dual-write)",
    )

    publish.add_argument("--force", action="store_true", help="Overwrite immutable pin")

    lst = sub.add_parser("list", help="List packages on a channel")

    lst.add_argument("--channel", default="lead")

    lst.add_argument("--json", action="store_true")

    search = sub.add_parser("search", help="Search packages")

    search.add_argument("query")

    search.add_argument("--channel", default="lead")

    search.add_argument("--json", action="store_true")

    show = sub.add_parser("show", help="Show package metadata")

    show.add_argument("package")

    show.add_argument("--channel", default="lead")

    show.add_argument("--json", action="store_true")

    download = sub.add_parser("download", help="Download package artifacts")

    download.add_argument("package")

    download.add_argument("-o", "--out", type=Path, default=Path("packs"))

    download.add_argument("--channel", default="lead")

    download.add_argument("--artifact", default=None, help="Single filename only")

    status_p = sub.add_parser("status", help="Show CDN deployment flags (experimental, version)")

    status_p.add_argument("--json", action="store_true")

    trust = sub.add_parser("trust", help="Admin trust-root CA store")

    trust_sub = trust.add_subparsers(dest="trust_cmd", required=True)

    trust_sub.add_parser("list", help="List trusted root CAs")

    trust_add = trust_sub.add_parser("add", help="Upload a root CA (PEM or DER)")

    trust_add.add_argument("cert", type=Path)

    trust_add.add_argument("--name", default="")

    trust_rm = trust_sub.add_parser("rm", help="Delete a trust root by id")

    trust_rm.add_argument("id")

    trust_status = trust_sub.add_parser(
        "status", help="Show current trust bundle / sub-CA policy"
    )

    trust_status.add_argument("--json", action="store_true")

    trust_fetch = trust_sub.add_parser(
        "fetch", help="Download the active MPTB bundle to a file"
    )

    trust_fetch.add_argument(
        "-o", "--out", type=Path, default=Path("bundle.mptb"), help="Output path"
    )

    trust_apply = trust_sub.add_parser(
        "apply",
        help="Rotate the active trust bundle (admin) from a signed MPTB file",
    )

    trust_apply.add_argument("bundle", type=Path, help="MPTB from: wasmmod cdn sign bundle-gen")

    trust_clear = trust_sub.add_parser(
        "clear", help="Clear the active trust bundle (admin) — back to allow-any"
    )

    inspect_p = sub.add_parser("inspect", help="Inspect a local artifact file")

    inspect_p.add_argument("file", type=Path)

    inspect_p.add_argument("--json", action="store_true")

    inspect_p.add_argument("--verify", action="store_true")

    inspect_p.add_argument(
        "--trust",
        type=Path,
        action="append",
        default=[],
        help="Root CA for --verify (repeatable)",
    )

    db = sub.add_parser("db", help="Database migrations (Alembic)")

    db_sub = db.add_subparsers(dest="db_cmd", required=True)

    db_sub.add_parser("upgrade", help="Apply Alembic migrations to head")

    db_sub.add_parser("current", help="Show current Alembic revision")

    return parser
