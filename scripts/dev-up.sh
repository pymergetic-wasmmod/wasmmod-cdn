#!/usr/bin/env bash
# One-shot local / public-test CDN: µPy (optional) → docker (auth on) → seed.
#
#   ./scripts/dev-up.sh              # full path
#   ./scripts/dev-up.sh --seed-only  # packs into already-running container
#   ./scripts/dev-up.sh --no-upy     # skip Emscripten rebuild (reuse synced assets)
#   ./scripts/dev-up.sh --no-seed    # docker only
#   ./scripts/dev-up.sh --reseed     # wipe named volume metal-cdn-data, then docker+seed
#
# Auth (public-test defaults):
#   ./scripts/ensure-secrets.sh      # once → .secrets/cdn.env (gitignored)
#   docker gets REQUIRE_AUTH=true + bootstrap admin; seed uses Bearer from .secrets/token
#
# Env overrides:
#   METALPYTHON   path to metalpython tree (default: sibling ../metalpython)
#   METAL_CDN_URL http://127.0.0.1:8000/cdn
#   METAL_CDN_IMAGE metal-cdn
#   METAL_CDN_NAME  metal-cdn
#   METAL_CDN_PORT  8000
#   METAL_CDN_VOLUME metal-cdn-data   # docker volume wiped by --reseed only
#   METAL_CDN_REQUIRE_SIGNED  off|present|verify (default: present)
#   METAL_CDN_REQUIRE_AUTH    default true for this script
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

IMAGE="${METAL_CDN_IMAGE:-metal-cdn}"
NAME="${METAL_CDN_NAME:-metal-cdn}"
PORT="${METAL_CDN_PORT:-8000}"
CDN_URL="${METAL_CDN_URL:-http://127.0.0.1:${PORT}/cdn}"
VOLUME="${METAL_CDN_VOLUME:-metal-cdn-data}"
SECRETS_DIR="$ROOT/.secrets"
SECRETS_ENV="$SECRETS_DIR/cdn.env"
SECRETS_TOKEN="$SECRETS_DIR/token"

DO_UPY=1
DO_DOCKER=1
DO_SEED=1
DO_RESEED=0
SEED_ONLY=0

usage() {
  sed -n '2,22p' "$0" | sed 's/^# \?//'
  exit "${1:-0}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage 0 ;;
    --no-upy) DO_UPY=0 ;;
    --no-seed) DO_SEED=0 ;;
    --no-docker) DO_DOCKER=0 ;;
    --seed-only) SEED_ONLY=1; DO_UPY=0; DO_DOCKER=0; DO_SEED=1; DO_RESEED=0 ;;
    --reseed) DO_RESEED=1; DO_DOCKER=1; DO_SEED=1 ;;
    *) echo "unknown arg: $1" >&2; usage 2 ;;
  esac
  shift
done

load_secrets() {
  "$ROOT/scripts/ensure-secrets.sh"
  # shellcheck disable=SC1090
  set -a
  # shellcheck disable=SC1091
  source "$SECRETS_ENV"
  set +a
  if [[ -z "${METAL_CDN_BOOTSTRAP_ADMIN_EMAIL:-}" || -z "${METAL_CDN_BOOTSTRAP_ADMIN_PASSWORD:-}" ]]; then
    echo "FAIL: $SECRETS_ENV missing bootstrap email/password" >&2
    exit 1
  fi
}

mint_or_load_token() {
  mkdir -p "$SECRETS_DIR"
  chmod 700 "$SECRETS_DIR" 2>/dev/null || true
  if [[ -f "$SECRETS_TOKEN" ]]; then
    local existing
    existing="$(tr -d '[:space:]' <"$SECRETS_TOKEN")"
    if [[ -n "$existing" ]]; then
      if curl -sf -H "Authorization: Bearer $existing" "$CDN_URL/auth/me" >/dev/null 2>&1; then
        echo "    reuse token → $SECRETS_TOKEN"
        printf '%s' "$existing" >"$SECRETS_TOKEN"
        chmod 600 "$SECRETS_TOKEN"
        return 0
      fi
      echo "    stale token — re-minting"
    fi
  fi
  local body code
  body="$(python3 -c 'import json,os; print(json.dumps({"email":os.environ["METAL_CDN_BOOTSTRAP_ADMIN_EMAIL"],"password":os.environ["METAL_CDN_BOOTSTRAP_ADMIN_PASSWORD"],"name":"dev-up-seed"}))')"
  code="$(curl -sS -o /tmp/metal-cdn-token.json -w '%{http_code}' -X POST "$CDN_URL/auth/token" \
    -H 'Content-Type: application/json' \
    -d "$body")"
  if [[ "$code" != "200" && "$code" != "201" ]]; then
    echo "FAIL: mint token HTTP $code — $(head -c 240 /tmp/metal-cdn-token.json)" >&2
    exit 1
  fi
  python3 -c 'import json; print(json.load(open("/tmp/metal-cdn-token.json"))["key"])' >"$SECRETS_TOKEN"
  chmod 600 "$SECRETS_TOKEN"
  echo "    minted token → $SECRETS_TOKEN"
}

seed_token() {
  if [[ ! -f "$SECRETS_TOKEN" ]]; then
    echo "FAIL: missing $SECRETS_TOKEN (run docker step or mint_or_load_token)" >&2
    exit 1
  fi
  tr -d '[:space:]' <"$SECRETS_TOKEN"
}

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

step_reseed_volume() {
  # Only the named local CDN volume (default metal-cdn-data). Never touches
  # compose volumes like pgdata / host bind mounts outside Docker.
  echo "==> reseed: stop $NAME and remove docker volume $VOLUME"
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  if docker volume inspect "$VOLUME" >/dev/null 2>&1; then
    docker volume rm "$VOLUME"
    echo "    removed volume $VOLUME"
  else
    echo "    volume $VOLUME already absent"
  fi
  # Volume wipe recreates DB users; drop cached API key so we re-mint.
  rm -f "$SECRETS_TOKEN"
}

step_docker() {
  load_secrets
  echo "==> sync wasmmod inspect helpers into client package"
  "$ROOT/scripts/sync-wasmmod-inspect.sh"
  echo "==> docker build -t $IMAGE"
  docker build -t "$IMAGE" "$ROOT"
  echo "==> docker run $NAME :$PORT (require_auth=true)"
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  local secret="${METAL_CDN_SESSION_SECRET:-}"
  if [[ -z "$secret" ]]; then
    secret="$(openssl rand -hex 32 2>/dev/null || head -c 32 /dev/urandom | xxd -p -c 32)"
  fi
  docker run -d --name "$NAME" -p "${PORT}:8000" \
    -e "METAL_CDN_SESSION_SECRET=$secret" \
    -e METAL_CDN_EXPERIMENTAL=true \
    -e METAL_CDN_EXPERIMENTAL_REPL=true \
    -e "METAL_CDN_PUBLIC_ORIGIN=${METAL_CDN_PUBLIC_ORIGIN:-https://cdn.pymergetic.com}" \
    -e "METAL_CDN_BEHIND_PROXY=${METAL_CDN_BEHIND_PROXY:-true}" \
    -e "METAL_CDN_REQUIRE_SIGNED=${METAL_CDN_REQUIRE_SIGNED:-present}" \
    -e "METAL_CDN_REQUIRE_AUTH=${METAL_CDN_REQUIRE_AUTH:-true}" \
    -e "METAL_CDN_ALLOW_OPEN_REGISTRATION=${METAL_CDN_ALLOW_OPEN_REGISTRATION:-false}" \
    -e "METAL_CDN_BOOTSTRAP_ADMIN_EMAIL=${METAL_CDN_BOOTSTRAP_ADMIN_EMAIL}" \
    -e "METAL_CDN_BOOTSTRAP_ADMIN_PASSWORD=${METAL_CDN_BOOTSTRAP_ADMIN_PASSWORD}" \
    -v "${VOLUME}:/data" \
    "$IMAGE"
  echo "==> wait for $CDN_URL/health"
  local i
  for i in $(seq 1 60); do
    if curl -sf "$CDN_URL/health" >/dev/null 2>&1; then
      echo "    healthy (${i}s)"
      echo "==> seed API token"
      mint_or_load_token
      echo "    login: ${METAL_CDN_BOOTSTRAP_ADMIN_EMAIL} (password in $SECRETS_ENV)"
      return 0
    fi
    sleep 1
  done
  echo "FAIL: CDN not healthy — docker logs $NAME" >&2
  docker logs "$NAME" 2>&1 | tail -40 >&2 || true
  exit 1
}

# Publish one package with one or more artifact files in a single request.
# (Lead/pin package entries replace artifacts wholesale — do not split twins
# across multiple publishes.)
# Usage: pub_pkg NAME [DEPS_JSON] FILE [FILE...]
pub_pkg() {
  local name="$1"
  shift
  local deps="{}"
  if [[ "${1:-}" == \{* ]]; then
    deps="$1"
    shift
  fi
  if [[ $# -lt 1 ]]; then
    echo "FAIL: pub_pkg $name needs at least one file" >&2
    return 1
  fi
  local meta code form=() token
  token="$(seed_token)"
  local f
  for f in "$@"; do
    if [[ ! -f "$f" ]]; then
      echo "FAIL: missing artifact $f" >&2
      return 1
    fi
    form+=(-F "files=@$f;type=application/octet-stream")
  done
  meta="$(python3 -c 'import json,sys; print(json.dumps({"package":sys.argv[1],"version":"0.1.0","lead":True,"pin":True,"force":True,"deps":json.loads(sys.argv[2])}))' "$name" "$deps")"
  echo -n "    publish $name ("
  local names=()
  for f in "$@"; do
    names+=("$(basename "$f")")
  done
  local IFS=,
  echo -n "${names[*]}"
  echo -n ") … "
  code="$(curl -sS -o /tmp/metal-cdn-dev-pub.json -w '%{http_code}' -X POST "$CDN_URL/publish" \
    -H "Authorization: Bearer $token" \
    -F "meta=$meta;type=application/json" \
    "${form[@]}")"
  echo "HTTP $code"
  if [[ "$code" != "201" && "$code" != "200" ]]; then
    echo "      $(head -c 240 /tmp/metal-cdn-dev-pub.json)" >&2
    return 1
  fi
}

step_seed() {
  local mp packs
  load_secrets
  if [[ ! -f "$SECRETS_TOKEN" ]]; then
    echo "==> seed API token"
    mint_or_load_token
  fi
  mp="$(find_metalpython)" || {
    echo "FAIL: metalpython not found (set METALPYTHON=…)" >&2
    exit 1
  }
  packs="$mp/extmod/wasmmod/examples/packs"
  examples="$mp/extmod/wasmmod/examples"
  echo "==> build + sign example packs (wasmmod.sig / examples/.keys)"
  # Prefer system cmake over broken emsdk shims, but keep the active
  # interpreter (venv) so tools/wasmmod.py can import pymergetic-wasmmod-tools.
  _py_bin="$(cd "$(dirname "$(command -v python3)")" && pwd)"
  PATH="${_py_bin}:/usr/bin:/bin:${PATH}" make -C "$examples" sign-packs
  echo "==> publish samples → $CDN_URL (Bearer)"
  [[ -f "$packs/hello.wasm" ]] || { echo "FAIL: missing $packs/hello.wasm" >&2; exit 1; }

  local hello_files=("$packs/hello.wasm")
  [[ -f "$packs/hello.elf" ]] && hello_files+=("$packs/hello.elf")
  [[ -f "$packs/hello.x86_64.elf" ]] && hello_files+=("$packs/hello.x86_64.elf")
  [[ -f "$packs/hello.aarch64.elf" ]] && hello_files+=("$packs/hello.aarch64.elf")
  pub_pkg hello "${hello_files[@]}"

  local client_files=("$packs/client.wasm")
  [[ -f "$packs/client.elf" ]] && client_files+=("$packs/client.elf")
  [[ -f "$packs/client.x86_64.elf" ]] && client_files+=("$packs/client.x86_64.elf")
  pub_pkg client '{"hello":"0.1.0"}' "${client_files[@]}"

  local host_files=()
  [[ -f "$packs/hostcall.elf" ]] && host_files+=("$packs/hostcall.elf")
  [[ -f "$packs/hostcall.x86_64.elf" ]] && host_files+=("$packs/hostcall.x86_64.elf")
  if [[ ${#host_files[@]} -gt 0 ]]; then
    pub_pkg hostcall "${host_files[@]}"
  fi

  local ticks_files=()
  [[ -f "$packs/ticks.wasm" ]] && ticks_files+=("$packs/ticks.wasm")
  [[ -f "$packs/ticks.elf" ]] && ticks_files+=("$packs/ticks.elf")
  if [[ ${#ticks_files[@]} -gt 0 ]]; then
    pub_pkg ticks "${ticks_files[@]}"
  fi

  pub_pkg mixed                "$packs/mixed.wasm"
  pub_pkg bridge               "$packs/bridge.wasm"
  pub_pkg test_a               "$packs/test_a.wasm"
  pub_pkg test_a.test_d        "$packs/test_a.test_d.wasm"
  pub_pkg test_a.test_b.test_c '{"test_a.test_d":"0.1.0"}' "$packs/test_a.test_b.test_c.wasm"
  pub_pkg test_a2              "$packs/test_a2.wasm"
  pub_pkg test_a2.test_d2      "$packs/test_a2.test_d2.wasm"
  pub_pkg test_a2.test_b2.test_c2 '{"test_a2.test_d2":"0.1.0"}' "$packs/test_a2.test_b2.test_c2.wasm"

  # First-party host engine (self-describing micropython.wasm + wasmmod.source).
  local eng_src eng_pub
  eng_src="$mp/ports/webassembly/build-wasmmod/pymergetic.wasmmod.wasm"
  [[ -f "$eng_src" ]] || eng_src="$mp/ports/webassembly/build-wasmmod/micropython.wasm"
  if [[ -f "$eng_src" ]]; then
    eng_pub="$packs/pymergetic.wasmmod.wasm"
    cp -f "$eng_src" "$eng_pub"
    echo "SIGN $eng_pub (host engine)"
    python3 "$mp/extmod/wasmmod/tools/wasmmod.py" sign sign \
      --key "$examples/.keys/sign/leaf.key.pem" \
      --chain "$examples/.keys/sign/chain.der" \
      "$eng_pub"
    pub_pkg pymergetic.wasmmod "$eng_pub"
  else
    echo "WARN: no host engine wasm at build-wasmmod — skip pymergetic.wasmmod publish" >&2
  fi

  echo "==> lead packages:"
  curl -sf "$CDN_URL/index/lead" | python3 -c \
    "import sys,json; print('   ', ', '.join(sorted(json.load(sys.stdin).get('packages',{}))))"
  echo -n "==> signature check hello.wasm … "
  curl -sf "$CDN_URL/artifacts/lead/hello.wasm/inspect" | python3 -c \
    'import sys,json; d=json.load(sys.stdin); ok=bool(d.get("signed") and d.get("sig")); print("signed" if ok else "UNSIGNED"); raise SystemExit(0 if ok else 1)'
  if [[ -f "$packs/hello.elf" ]]; then
    echo -n "==> signature check hello.elf … "
    curl -sf "$CDN_URL/artifacts/lead/hello.elf/inspect" | python3 -c \
      'import sys,json; d=json.load(sys.stdin); ok=bool(d.get("signed") and d.get("sig")); print("signed" if ok else "UNSIGNED"); raise SystemExit(0 if ok else 1)'
  fi
}

# ── main ──────────────────────────────────────────────────────────────
if [[ "$DO_RESEED" -eq 1 ]]; then
  if [[ "$SEED_ONLY" -eq 1 ]]; then
    echo "FAIL: --reseed cannot combine with --seed-only (needs docker recreate)" >&2
    exit 2
  fi
  step_reseed_volume
fi

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
echo "    auth   REQUIRE_AUTH on — login ${METAL_CDN_BOOTSTRAP_ADMIN_EMAIL:-demo@…}"
echo "    secrets $SECRETS_ENV  |  token $SECRETS_TOKEN"
echo "    shell  open µPy panel → packages()  |  import hello"
echo "    stop   docker rm -f $NAME"
