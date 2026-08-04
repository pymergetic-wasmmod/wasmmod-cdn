#!/usr/bin/env bash
# Copy ports/webassembly build outputs into metal-cdn static/repl/ (gitignored).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$ROOT/pymergetic/metal/cdn/web/static/repl"
SRC="${1:-}"
if [[ -z "$SRC" || ! -d "$SRC" ]]; then
  echo "usage: $0 /path/to/ports/webassembly/build-standard" >&2
  exit 2
fi
mkdir -p "$DEST"
for f in micropython.mjs micropython.wasm micropython.min.mjs; do
  if [[ -f "$SRC/$f" ]]; then
    cp -f "$SRC/$f" "$DEST/$f"
    echo "copied $f"
  fi
done
if [[ ! -f "$DEST/micropython.mjs" ]]; then
  echo "FAIL: no micropython.mjs in $SRC" >&2
  exit 1
fi
echo "OK → $DEST"
