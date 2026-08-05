"""CLI entry: ``metal-cdn serve|login|publish|…``."""

from __future__ import annotations

import uvicorn

from pymergetic.metal.cdn.cli_cmds import (
    _cmd_claim,
    _cmd_db,
    _cmd_download,
    _cmd_inspect,
    _cmd_list,
    _cmd_login,
    _cmd_logout,
    _cmd_publish,
    _cmd_search,
    _cmd_show,
    _cmd_status,
    _cmd_trust,
    _cmd_whoami,
)
from pymergetic.metal.cdn.cli_parser import build_parser
from pymergetic.metal.cdn.cli_util import PROG
from pymergetic.metal.cdn.settings import get_settings
from pymergetic.metal.cdn_client import invoke


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
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


if __name__ == "__main__":
    raise SystemExit(invoke(main, prog=PROG))
