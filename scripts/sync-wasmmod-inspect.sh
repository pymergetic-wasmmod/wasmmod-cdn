#!/usr/bin/env bash
# Copy shared wasmmod inspect helpers into the CDN client package so Docker
# images (and hosts without a sibling metalpython tree) can resolve symbols.
#
# Source of truth: metalpython/extmod/wasmmod/tools/{wasmmod_inspect,wasmmod_elf}.py
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$ROOT/pymergetic/metal/cdn_client/wasmmod_tools"

find_metalpython() {
  if [[ -n "${METALPYTHON:-}" ]]; then
    echo "$METALPYTHON"
    return 0
  fi
  local c
  for c in \
    "$ROOT/../metalpython" \
    "$ROOT/../../metalpython" \
    "$HOME/Devel/os-sdk/packages/metalpython"
  do
    if [[ -d "$c/extmod/wasmmod/tools" ]]; then
      echo "$c"
      return 0
    fi
  done
  return 1
}

mp="$(find_metalpython)" || {
  echo "FAIL: metalpython not found (set METALPYTHON=…)" >&2
  exit 1
}
tools="$mp/extmod/wasmmod/tools"
for f in wasmmod_inspect.py wasmmod_elf.py; do
  [[ -f "$tools/$f" ]] || { echo "FAIL: missing $tools/$f" >&2; exit 1; }
done

mkdir -p "$DEST"
cp -f "$tools/wasmmod_inspect.py" "$tools/wasmmod_elf.py" "$DEST/"
# Drop CLI-only side effects note for packagers.
cat > "$DEST/README.md" <<'EOF'
# Bundled wasmmod inspect helpers

Synced from `metalpython/extmod/wasmmod/tools/` by `scripts/sync-wasmmod-inspect.sh`
(also run from `scripts/dev-up.sh` before `docker build`).

Do not edit here — change the wasmmod originals and re-sync.
EOF

echo "OK synced wasmmod_inspect → $DEST"
