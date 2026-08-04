#!/usr/bin/env bash
# One-shot local CDN: build browser µPy (optional) → sync → docker → seed samples.
#
#   ./scripts/dev-up.sh              # full path
#   ./scripts/dev-up.sh --seed-only  # packs into already-running container
#   ./scripts/dev-up.sh --no-upy     # skip Emscripten rebuild (reuse synced assets)
#   ./scripts/dev-up.sh --no-seed    # docker only
#
# Env overrides:
#   METALPYTHON   path to metalpython tree (default: sibling ../metalpython)
#   METAL_CDN_URL http://127.0.0.1:8000/cdn
#   METAL_CDN_IMAGE metal-cdn
#   METAL_CDN_NAME  metal-cdn
#   METAL_CDN_PORT  8000
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

IMAGE="${METAL_CDN_IMAGE:-metal-cdn}"
NAME="${METAL_CDN_NAME:-metal-cdn}"
PORT="${METAL_CDN_PORT:-8000}"
CDN_URL="${METAL_CDN_URL:-http://127.0.0.1:${PORT}/cdn}"

DO_UPY=1
DO_DOCKER=1
DO_SEED=1
SEED_ONLY=0

usage() {
  sed -n '2,14p' "$0" | sed 's/^# \?//'
  exit "${1:-0}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage 0 ;;
    --no-upy) DO_UPY=0 ;;
    --no-seed) DO_SEED=0 ;;
    --no-docker) DO_DOCKER=0 ;;
    --seed-only) SEED_ONLY=1; DO_UPY=0; DO_DOCKER=0; DO_SEED=1 ;;
    *) echo "unknown arg: $1" >&2; usage 2 ;;
  esac
  shift
done

find_metalpython() {
  if [[ -n "${METALPYTHON:-}" ]]; then
    echo "$METALPYTHON"
    return
  fi
  local cand
  for cand in \
    "$ROOT/../metalpython" \
    "$ROOT/../../metalpython" \
    "$HOME/Devel/os-sdk/packages/metalpython"
  do
    if [[ -d "$cand/extmod/wasmmod/ports/micropython/webassembly" ]]; then
      echo "$(cd "$cand" && pwd)"
      return
    fi
  done
  return 1
}

ensure_emsdk() {
  if command -v emcc >/dev/null 2>&1; then
    return 0
  fi
  local envf
  for envf in "${EMSDK}/emsdk_env.sh" "$HOME/emsdk/emsdk_env.sh"; do
    if [[ -f "$envf" ]]; then
      # shellcheck disable=SC1090
      source "$envf"
      return 0
    fi
  done
  echo "FAIL: emcc not on PATH; source emsdk_env.sh or set EMSDK" >&2
  exit 1
}

step_upy() {
  local mp build
  mp="$(find_metalpython)" || {
    echo "FAIL: metalpython not found (set METALPYTHON=…)" >&2
    exit 1
  }
  build="$mp/ports/webassembly/build-wasmmod"
  echo "==> build browser µPy+wasmmod  ($mp)"
  ensure_emsdk
  make -C "$mp/extmod/wasmmod/ports/micropython/webassembly" -j"$(nproc 2>/dev/null || echo 4)"
  echo "==> sync REPL assets"
  "$ROOT/scripts/sync-repl-assets.sh" "$build"
}

step_docker() {
  echo "==> docker build -t $IMAGE"
  docker build -t "$IMAGE" "$ROOT"
  echo "==> docker run $NAME :$PORT"
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  local secret="${METAL_CDN_SESSION_SECRET:-}"
  if [[ -z "$secret" ]]; then
    secret="$(openssl rand -hex 32 2>/dev/null || head -c 32 /dev/urandom | xxd -p -c 32)"
  fi
  docker run -d --name "$NAME" -p "${PORT}:8000" \
    -e "METAL_CDN_SESSION_SECRET=$secret" \
    -e METAL_CDN_EXPERIMENTAL=true \
    -e METAL_CDN_EXPERIMENTAL_REPL=true \
    -v metal-cdn-data:/data \
    "$IMAGE"
  echo "==> wait for $CDN_URL/health"
  local i
  for i in $(seq 1 60); do
    if curl -sf "$CDN_URL/health" >/dev/null 2>&1; then
      echo "    healthy (${i}s)"
      return 0
    fi
    sleep 1
  done
  echo "FAIL: CDN not healthy — docker logs $NAME" >&2
  docker logs "$NAME" 2>&1 | tail -40 >&2 || true
  exit 1
}

pub_one() {
  local name="$1" file="$2"
  # Do not use ${3:-{}} — bash parses the closing braces wrong and appends a stray }.
  local deps="{}"
  if [[ -n "${3:-}" ]]; then
    deps="$3"
  fi
  local meta code
  meta="$(python3 -c 'import json,sys; print(json.dumps({"package":sys.argv[1],"version":"0.1.0","lead":True,"pin":True,"force":True,"deps":json.loads(sys.argv[2])}))' "$name" "$deps")"
  echo -n "    publish $name … "
  code="$(curl -sS -o /tmp/metal-cdn-dev-pub.json -w '%{http_code}' -X POST "$CDN_URL/publish" \
    -F "meta=$meta;type=application/json" \
    -F "files=@$file;type=application/octet-stream")"
  echo "HTTP $code"
  if [[ "$code" != "201" && "$code" != "200" ]]; then
    echo "      $(head -c 240 /tmp/metal-cdn-dev-pub.json)" >&2
    return 1
  fi
}

step_seed() {
  local mp packs
  mp="$(find_metalpython)" || {
    echo "FAIL: metalpython not found (set METALPYTHON=…)" >&2
    exit 1
  }
  packs="$mp/extmod/wasmmod/examples/packs"
  echo "==> build example packs"
  # Prefer system cmake over broken emsdk shims on PATH
  PATH="/usr/bin:/bin:${PATH}" make -C "$mp/extmod/wasmmod/examples" packs
  echo "==> publish samples → $CDN_URL"
  [[ -f "$packs/hello.wasm" ]] || { echo "FAIL: missing $packs/hello.wasm" >&2; exit 1; }
  pub_one hello                "$packs/hello.wasm"
  pub_one client               "$packs/client.wasm"
  pub_one mixed                "$packs/mixed.wasm"
  pub_one bridge               "$packs/bridge.wasm"
  pub_one test_a               "$packs/test_a.wasm"
  pub_one test_a.test_d        "$packs/test_a.test_d.wasm"
  pub_one test_a.test_b.test_c "$packs/test_a.test_b.test_c.wasm" '{"test_a.test_d":"0.1.0"}'
  pub_one test_a2              "$packs/test_a2.wasm"
  pub_one test_a2.test_d2      "$packs/test_a2.test_d2.wasm"
  pub_one test_a2.test_b2.test_c2 "$packs/test_a2.test_b2.test_c2.wasm" '{"test_a2.test_d2":"0.1.0"}'
  echo "==> lead packages:"
  curl -sf "$CDN_URL/index/lead" | python3 -c \
    "import sys,json; print('   ', ', '.join(sorted(json.load(sys.stdin).get('packages',{}))))"
}

# ── main ──────────────────────────────────────────────────────────────
if [[ "$SEED_ONLY" -eq 0 && "$DO_UPY" -eq 1 ]]; then
  step_upy
elif [[ "$DO_DOCKER" -eq 1 ]]; then
  # docker still needs assets if missing
  if [[ ! -f "$ROOT/pymergetic/metal/cdn/web/static/repl/micropython.mjs" ]]; then
    echo "note: no synced micropython.mjs — building µPy first"
    step_upy
  fi
fi

if [[ "$DO_DOCKER" -eq 1 ]]; then
  step_docker
fi

if [[ "$DO_SEED" -eq 1 ]]; then
  if ! curl -sf "$CDN_URL/health" >/dev/null 2>&1; then
    echo "FAIL: CDN not reachable at $CDN_URL (start with ./scripts/dev-up.sh or --no-seed)" >&2
    exit 1
  fi
  step_seed
fi

echo
echo "OK  UI     $CDN_URL/"
echo "    shell  open µPy panel → packages()  |  import hello"
echo "    stop   docker rm -f $NAME"
