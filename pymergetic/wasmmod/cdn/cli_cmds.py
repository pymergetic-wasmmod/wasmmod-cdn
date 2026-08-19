"""CLI command handlers."""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path

from pymergetic.wasmmod.cdn.cli_util import (
    PROG,
    client_from_config,
    fail,
    warn_experimental,
)
from pymergetic.wasmmod.cdn.settings import get_settings
from pymergetic.wasmmod.cdn_client import (
    TOKEN_SOURCE_API_KEY,
    CdnClient,
    ClientError,
    clear_token,
    format_error,
    load_config,
    report,
    save_config,
)
from pymergetic.wasmmod.cdn_client.trust import parse_mptb


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
        report(PROG, "alembic.ini not found", "Run from wasmmod-cdn repo root")
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
        return fail(exc)
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
        client = client_from_config()
        warn_experimental(client)
        me = client.me()
    except ClientError as exc:
        return fail(exc)
    print(json.dumps(me, indent=2))
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    try:
        client = client_from_config()
        data = client.status()
    except ClientError as exc:
        return fail(exc)
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
        client = client_from_config()
        warn_experimental(client)
        result = client.claim(package)
    except ClientError as exc:
        return fail(exc)
    print(json.dumps(result, indent=2))
    return 0


def _cmd_publish(args: argparse.Namespace) -> int:
    missing = [p for p in args.files if not p.is_file()]
    if missing:
        report(PROG, f"missing files: {', '.join(str(p) for p in missing)}")
        return 2
    try:
        client = client_from_config()
        warn_experimental(client)
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
            upstream=args.upstream,
            also_local=args.also_local,
        )
    except ClientError as exc:
        return fail(exc, force=args.force)
    print(json.dumps(result, indent=2))
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    try:
        rows = client_from_config().list_packages(channel=args.channel)
    except ClientError as exc:
        return fail(exc)
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
        rows = client_from_config().search(args.query, channel=args.channel)
    except ClientError as exc:
        return fail(exc)
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
        entry = client_from_config().get_package(args.package, channel=args.channel)
    except ClientError as exc:
        return fail(exc)
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
        client = client_from_config()
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
        return fail(exc)
    return 0


def _cmd_trust(args: argparse.Namespace) -> int:
    try:
        client = client_from_config()
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
        if args.trust_cmd == "status":
            policy = client.get_trust_policy()
            if args.json:
                print(json.dumps(policy, indent=2, default=str))
                return 0
            applied = bool(policy.get("applied"))
            print(f"applied={applied}  allow={policy.get('allow', 0)}  deny={policy.get('deny', 0)}")
            print(f"bundle_sha256={policy.get('bundle_sha256')}")
            if policy.get("issued"):
                print(f"issued={policy.get('issued')}  expires={policy.get('expires')}")
            return 0
        if args.trust_cmd == "fetch":
            blob = client.get_trust_bundle()
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_bytes(blob)
            print(f"wrote {args.out} ({len(blob)}B)")
            return 0
        if args.trust_cmd == "apply":
            if not args.bundle.is_file():
                report(PROG, f"not a file: {args.bundle}")
                return 2
            blob = args.bundle.read_bytes()
            try:
                parse_mptb(blob)
            except ValueError as exc:
                report(PROG, f"invalid MPTB: {exc}")
                return 2
            client.put_trust_bundle(blob, filename=args.bundle.name)
            print(f"applied {args.bundle.name} ({len(blob)}B)")
            return 0
        if args.trust_cmd == "clear":
            client.clear_trust_bundle()
            print("cleared active trust bundle")
            return 0
    except ClientError as exc:
        return fail(exc)
    return 1


def _cmd_inspect(args: argparse.Namespace) -> int:
    from pymergetic.wasmmod.cdn_client.contents import inspect_artifact

    if not args.file.is_file():
        report(PROG, f"not a file: {args.file}")
        return 2
    data = args.file.read_bytes()
    if args.verify:
        if not args.trust:
            report(PROG, "--verify requires --trust")
            return 2
        from pymergetic.wasmmod.cdn_client.verify import verify_artifact

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
