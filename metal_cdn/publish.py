#!/usr/bin/env python3
"""Publish stub for wasmmod packs into a CDN channel tree.

Scaffold only — does not upload or write artifacts yet.

Intended pipeline (when implemented):

  1. Build packs with wasmmod tooling
       python3 tools/wasmmod.py pack <pack-dir> -o hello.wasm [--aot]
  2. Sign naked artifacts (before MPZL wrap)
       python3 tools/wasmmod.py sign sign --key … --chain … hello.wasm
       python3 tools/wasmmod.py sign sign --key … --chain … hello.aot6
  3. Optional whole-artifact zlib (MPZL)
       python3 tools/wasmmod.py zlib wrap hello.wasm
       python3 tools/wasmmod.py zlib wrap hello.aot6
  4. Layout under a static root::

       packs/                 # lead / latest channel
       packs/@0.1.0/          # version pin
         hello.wasm.zlib
         hello.x86_64.aot6.zlib
         index.json           # see docs/INDEX.md

  5. Serve with any static HTTP server (wasmmod examples use
     ``tools/wasmmod.py httpd``). Clients resolve via
     ``wasm.install_hook(url=[primary, mirror, …])``.

Pack format / finder contract: https://github.com/pymergetic/wasmmod
(docs/PACK.md).
"""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="metal-cdn",
        description="CDN publish tooling for wasmmod packs (scaffold).",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser(
        "publish",
        help="Write packs/ + packs/@version/ + index.json (not implemented yet)",
    )
    p.add_argument(
        "artifacts",
        nargs="*",
        help="Signed .wasm / .aotN (and optional .zlib) paths from wasmmod",
    )
    p.add_argument(
        "-o",
        "--out",
        default="packs",
        help="Output channel root (default: packs/)",
    )
    p.add_argument(
        "--version",
        help="Pin directory name, e.g. 0.1.0 → packs/@0.1.0/",
    )
    p.add_argument(
        "--lead",
        action="store_true",
        help="Also publish into the lead/latest channel root",
    )

    args = ap.parse_args(argv)
    if args.cmd == "publish":
        print(
            "metal-cdn publish: scaffold only — not implemented yet.\n"
            "See docs/LAYOUT.md, docs/INDEX.md, docs/ROADMAP.md.\n"
            f"  out={args.out!r} version={args.version!r} "
            f"lead={args.lead} artifacts={list(args.artifacts)}",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
