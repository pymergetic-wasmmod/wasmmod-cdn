#!/usr/bin/env bash
# Build signed-ready CDN artifacts for metal platform packs (Inspect / Play-off).
# Uses wasmmod pack tooling — sections live on the artifact (not metal gut embeds).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

find_metalpython() {
  local c
  for c in \
    "${METALPYTHON:-}" \
    "$ROOT/../metalpython" \
    "$ROOT/../../metalpython"; do
    if [[ -n "$c" && -d "$c/extmod/wasmmod" && -d "$c/extmod/metal" ]]; then
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
METAL="$mp/extmod/metal"
WASMMOD="$mp/extmod/wasmmod"
TOOLS_SRC="$WASMMOD/dev/tools/src"
OUT="${1:-$WASMMOD/examples/packs}"
STAGING="${OUT}/.platform-src"
export PYTHONPATH="$TOOLS_SRC${PYTHONPATH:+:$PYTHONPATH}"

if ! python3 -c 'import pymergetic.wasmmod.tools' >/dev/null 2>&1; then
  echo "FAIL: pymergetic.wasmmod.tools missing (pip install -e $WASMMOD/dev/tools)" >&2
  exit 1
fi

mkdir -p "$OUT" "$STAGING/metal/src" "$STAGING/inspect"

# ── pymergetic.metal (kernel) ─────────────────────────────────────────
printf '%s\n' 'void pm_pack_load(void) {}' 'void pm_pack_unload(void) {}' \
  >"$STAGING/metal/src/stub.c"
cp -f "$METAL/httpd.json" "$STAGING/metal/src/httpd.json"
cat >"$STAGING/metal/pack.toml" <<'EOF'
type = "package"
name = "pymergetic.metal"
version = "0.1.0"
impl = ["c"]
description = "Metal ASGI host / kernel face (Inspect only; Play off)."
[python]
keep_source = true
freeze = false
[source]
embed = true
[pack]
compress = false
EOF

echo "==> pack pymergetic.metal → $OUT/pymergetic.metal.wasm"
python3 -m pymergetic.wasmmod.tools pack "$STAGING/metal" \
  -o "$OUT/pymergetic.metal.wasm" \
  --name pymergetic.metal \
  --pkg-version 0.1.0 \
  --tag role=kernel --tag product=metal --tag org=pymergetic

# ── pymergetic.metal.inspect (app / platform Play-off) ────────────────
rm -rf "$STAGING/inspect"
mkdir -p "$STAGING/inspect/src"
for f in __init__.py app.py adapter_fastapi.py adapter_microdot.py \
  dispatch.py self_desc.py stubs.py; do
  cp -f "$METAL/src/pymergetic/metal/inspect/$f" "$STAGING/inspect/src/$f"
done
mkdir -p "$STAGING/inspect/src/www"
cp -a "$METAL/src/pymergetic/metal/inspect/www/inspect" "$STAGING/inspect/src/www/inspect"
printf '%s\n' 'void pm_pack_load(void) {}' 'void pm_pack_unload(void) {}' \
  >"$STAGING/inspect/src/stub.c"
cat >"$STAGING/inspect/pack.toml" <<'EOF'
type = "package"
name = "pymergetic.metal.inspect"
version = "0.1.0"
impl = ["c"]
description = "Metal Inspect contract + UI (Inspect only; Play off)."
[python]
keep_source = true
freeze = false
[source]
embed = true
[pack]
compress = false
EOF

echo "==> pack pymergetic.metal.inspect → $OUT/pymergetic.metal.inspect.wasm"
python3 -m pymergetic.wasmmod.tools pack "$STAGING/inspect" \
  -o "$OUT/pymergetic.metal.inspect.wasm" \
  --name pymergetic.metal.inspect \
  --pkg-version 0.1.0 \
  --tag role=kernel --tag product=metal --tag org=pymergetic \
  --tag face=inspect

echo "OK platform packs:"
ls -la "$OUT/pymergetic.metal.wasm" "$OUT/pymergetic.metal.inspect.wasm"
