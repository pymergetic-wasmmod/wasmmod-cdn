#!/usr/bin/env bash
# Copy ports/webassembly build outputs into wasmmod-cdn static/repl/ (gitignored).
#
# Usage:
#   sync-repl-assets.sh /path/to/build-dir [engine-id]
#
# engine-id: mp | upywm | upy  (default: mp)
# Writes static/repl/<engine-id>/{micropython.mjs,micropython.wasm,…}
# Also mirrors mp/upywm to static/repl/ for legacy flat paths.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="${1:-}"
ENGINE="${2:-mp}"
DEST_ROOT="$ROOT/pymergetic/wasmmod/cdn/web/static/repl"
DEST="$DEST_ROOT/$ENGINE"

if [[ -z "$SRC" || ! -d "$SRC" ]]; then
  echo "usage: $0 /path/to/ports/webassembly/build-* [mp|upywm|upy]" >&2
  exit 2
fi
case "$ENGINE" in
  mp|upywm|upy) ;;
  mpwm|mp-wm) ENGINE=upywm; DEST="$DEST_ROOT/$ENGINE" ;;
  *)
    echo "FAIL: engine-id must be mp, upywm, or upy (got $ENGINE)" >&2
    exit 2
    ;;
esac

mkdir -p "$DEST"
for f in micropython.mjs micropython.wasm micropython.min.mjs; do
  if [[ -f "$SRC/$f" ]]; then
    cp -f "$SRC/$f" "$DEST/$f"
    echo "copied $ENGINE/$f"
  fi
done
if [[ ! -f "$DEST/micropython.mjs" ]]; then
  echo "FAIL: no micropython.mjs in $SRC" >&2
  exit 1
fi

if [[ "$ENGINE" == "upywm" || "$ENGINE" == "mp" ]]; then
  for f in micropython.mjs micropython.wasm micropython.min.mjs; do
    if [[ -f "$DEST/$f" ]]; then
      cp -f "$DEST/$f" "$DEST_ROOT/$f"
    fi
  done
  echo "mirrored $ENGINE → $DEST_ROOT/ (legacy)"
fi

echo "OK → $DEST"
