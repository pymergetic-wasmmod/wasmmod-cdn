"""CLI entry: ``metal-cdn serve|login|publish|…``."""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path

import uvicorn

from pymergetic.metal.cdn import __version__
from pymergetic.metal.cdn.settings import get_settings
from pymergetic.metal.cdn_client import (
    TOKEN_SOURCE_API_KEY,
    CdnClient,
    ClientError,
    clear_token,
    format_error,
    invoke,
    load_config,
    report,
    report_client_error,
    save_config,
)

PROG = "metal-cdn"


def _fail(exc: ClientError, *, force: bool = False) -> int:
    return report_client_error(exc, prog=PROG, force=force)


def _warn_experimental(client: CdnClient) -> None:
    try:
        status = client.status()
    except ClientError:
        return
    if not status.get("experimental"):
        return
    msg = status.get("experimental_message") or (
        "Experimental CDN: data will be wiped — often. "
        "Short tests only; do not run weekend-long experiments against it. Not for production."
    )
    report(PROG, msg, "Set METAL_CDN_EXPERIMENTAL=false after go-live")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="metal-cdn", description="metal-cdn CDN server & client")
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

    args = parser.parse_args(argv)

    if args.cmd == "serve":
        settings = get_settings()
        host = args.host or settings.host
        port = args.port or settings.port
        uvicorn.run("pymergetic.metal.cdn.main:app", host=host, port=port, reload=args.reload)
        return 0

    if args.cmd == "db":
        return _cmd_db(args.db_cmd)
    if args.cmd == "login":
        return _cmd_login(args)
    if args.cmd == "logout":
        return _cmd_logout()
    if args.cmd == "whoami":
        return _cmd_whoami()
    if args.cmd == "claim":
        return _cmd_claim(args.package)
    if args.cmd == "publish":
        return _cmd_publish(args)
    if args.cmd == "list":
        return _cmd_list(args)
    if args.cmd == "search":
        return _cmd_search(args)
    if args.cmd == "show":
        return _cmd_show(args)
    if args.cmd == "download":
        return _cmd_download(args)
    if args.cmd == "status":
        return _cmd_status(args)
    if args.cmd == "trust":
        return _cmd_trust(args)
    if args.cmd == "inspect":
        return _cmd_inspect(args)
    return 1


def _cmd_db(action: str) -> int:
    from pathlib import Path

    from alembic.config import Config

    from alembic import command

    candidates = [
        Path.cwd() / "alembic.ini",
        Path(__file__).resolve().parents[3] / "alembic.ini",
    ]
    ini = next((p for p in candidates if p.is_file()), None)
    if ini is None:
        report(PROG, "alembic.ini not found", "Run from metal-cdn repo root")
        return 2
    cfg = Config(str(ini))
    settings = get_settings()
    cfg.set_main_option("sqlalchemy.url", settings.database_url)
    if action == "upgrade":
        command.upgrade(cfg, "head")
        print("database upgraded to head")
        return 0
    if action == "current":
        command.current(cfg)
        return 0
    return 1


def _client_from_config() -> CdnClient:
    return CdnClient.from_config()


def _cmd_login(args: argparse.Namespace) -> int:
    cfg = load_config()
    url = args.url or cfg.get("url")
    if not url:
        report(PROG, "--url required", "e.g. http://127.0.0.1:8000/cdn")
        return 2
    if args.token:
        path = save_config(
            {
                "url": url.rstrip("/"),
                "token": args.token,
                "token_source": TOKEN_SOURCE_API_KEY,
            }
        )
        # Best-effort identity check
        try:
            me = CdnClient(url, token=args.token).me()
            print(f"logged in as {me.get('email', '?')}")
        except ClientError as exc:
            print(
                format_error(PROG, f"token stored but /auth/me failed: {exc}"),
                file=sys.stderr,
            )
        print(f"config: {path}")
        return 0
    email = args.email or input("Email: ").strip()
    password = args.password or getpass.getpass("Password: ")
    client = CdnClient(url)
    try:
        if args.register:
            client.register(email, password)
            print(f"registered {email}")
        created = client.create_api_key_with_password(email, password, name=args.name)
    except ClientError as exc:
        return _fail(exc)
    path = save_config(
        {
            "url": url.rstrip("/"),
            "token": created["key"],
            "email": email,
            "token_source": TOKEN_SOURCE_API_KEY,
        }
    )
    print(f"logged in as {email}")
    print(f"api key prefix: {created['prefix']}")
    print(f"config: {path}")
    return 0


def _cmd_logout() -> int:
    clear_token()
    print("logged out")
    return 0


def _cmd_whoami() -> int:
    try:
        client = _client_from_config()
        _warn_experimental(client)
        me = client.me()
    except ClientError as exc:
        return _fail(exc)
    print(json.dumps(me, indent=2))
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    try:
        client = _client_from_config()
        data = client.status()
    except ClientError as exc:
        return _fail(exc)
    if args.json:
        print(json.dumps(data, indent=2))
        return 0
    print(f"version: {data.get('version')}")
    exp = bool(data.get("experimental"))
    print(f"experimental: {exp}")
    if exp and data.get("experimental_message"):
        print(f"warning: {data['experimental_message']}")
    return 0


def _cmd_claim(package: str) -> int:
    try:
        client = _client_from_config()
        _warn_experimental(client)
        result = client.claim(package)
    except ClientError as exc:
        return _fail(exc)
    print(json.dumps(result, indent=2))
    return 0


def _cmd_publish(args: argparse.Namespace) -> int:
    missing = [p for p in args.files if not p.is_file()]
    if missing:
        report(PROG, f"missing files: {', '.join(str(p) for p in missing)}")
        return 2
    try:
        client = _client_from_config()
        _warn_experimental(client)
        if args.claim:
            client.claim(args.package)
        result = client.publish(
            package=args.package,
            version=args.version,
            files=args.files,
            lead=not args.no_lead,
            pin=not args.no_pin,
            aot_version=args.aot_version,
            force=args.force,
        )
    except ClientError as exc:
        return _fail(exc, force=args.force)
    print(json.dumps(result, indent=2))
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    try:
        rows = _client_from_config().list_packages(channel=args.channel)
    except ClientError as exc:
        return _fail(exc)
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    for row in rows:
        if isinstance(row, dict):
            print(f"{row.get('name')} {row.get('version')} arts={row.get('artifact_count')}")
        else:
            print(row)
    return 0


def _cmd_search(args: argparse.Namespace) -> int:
    try:
        rows = _client_from_config().search(args.query, channel=args.channel)
    except ClientError as exc:
        return _fail(exc)
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    for row in rows:
        if isinstance(row, dict):
            print(f"{row.get('name')} {row.get('version')} — {row.get('description') or ''}")
        else:
            print(row)
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    try:
        entry = _client_from_config().get_package(args.package, channel=args.channel)
    except ClientError as exc:
        return _fail(exc)
    print(json.dumps(entry, indent=2))
    return 0


def _cmd_download(args: argparse.Namespace) -> int:
    pin = args.channel[1:] if args.channel.startswith("@") else None
    if args.channel not in ("lead",) and not args.channel.startswith("@"):
        pin = args.channel
        channel = f"@{args.channel}"
    else:
        channel = args.channel
    try:
        client = _client_from_config()
        entry = client.get_package(args.package, channel=channel)
        arts = entry.get("artifacts") or []
        names: list[str] = []
        for art in arts:
            if isinstance(art, dict):
                fn = str(art.get("path") or art.get("filename") or "")
            else:
                fn = str(art)
            if fn:
                names.append(Path(fn).name)
        if args.artifact:
            names = [args.artifact]
        if not names:
            report(PROG, "no artifacts on package")
            return 1
        args.out.mkdir(parents=True, exist_ok=True)
        for name in names:
            dl = client.download_artifact(name, version=pin)
            if dl.data is None:
                report(PROG, f"empty download for {name}")
                return 1
            dest = args.out / Path(name).name
            dest.write_bytes(dl.data)
            print(dest)
    except ClientError as exc:
        return _fail(exc)
    return 0


def _cmd_trust(args: argparse.Namespace) -> int:
    try:
        client = _client_from_config()
        if args.trust_cmd == "list":
            rows = client.list_trust()
            print(json.dumps(rows, indent=2, default=str))
            return 0
        if args.trust_cmd == "add":
            if not args.cert.is_file():
                report(PROG, f"not a file: {args.cert}")
                return 2
            row = client.add_trust(args.cert, name=args.name)
            print(json.dumps(row, indent=2, default=str))
            return 0
        if args.trust_cmd == "rm":
            client.delete_trust(args.id)
            print(f"deleted {args.id}")
            return 0
    except ClientError as exc:
        return _fail(exc)
    return 1


def _cmd_inspect(args: argparse.Namespace) -> int:
    from pymergetic.metal.cdn_client.contents import inspect_artifact

    if not args.file.is_file():
        report(PROG, f"not a file: {args.file}")
        return 2
    data = args.file.read_bytes()
    if args.verify:
        if not args.trust:
            report(PROG, "--verify requires --trust")
            return 2
        from pymergetic.metal.cdn_client.verify import verify_artifact

        roots = [p.read_bytes() for p in args.trust]
        result = verify_artifact(data, trust_roots=roots, filename=args.file.name)
        if args.json:
            print(
                json.dumps(
                    {
                        "ok": result.ok,
                        "error": result.error,
                        "signed": result.signed,
                        "format": result.format,
                        "leaf_sha256": result.leaf_sha256,
                    },
                    indent=2,
                )
            )
        else:
            if result.ok:
                print(f"verify: ok ({result.format})")
            else:
                print(f"verify: FAIL — {result.error}", file=sys.stderr)
                return 1
    contents = inspect_artifact(data, filename=args.file.name)
    dump = contents.model_dump()
    if args.json:
        print(json.dumps(dump, indent=2, default=str))
    else:
        print(
            f"kind={dump.get('kind')} encoding={dump.get('encoding')} signed={dump.get('signed')}"
        )
        if dump.get("pack"):
            print(f"pack={dump['pack'].get('name')} files={len(dump['pack'].get('files') or [])}")
        if dump.get("source"):
            print(
                f"source={dump['source'].get('name')} files={len(dump['source'].get('files') or [])}"
            )
        if dump.get("sig"):
            print(f"sig={dump['sig'].get('format')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(invoke(main, prog=PROG))
