#!/usr/bin/env bash
# One-shot local / public-test CDN (+ optional freestanding firmware / bootserver).
#
# Convenience (everything):
#   ./scripts/dev-up.sh --full        # browser engines + docker + seed + firmware + bootserver
#   ./scripts/dev-up.sh --all         # same as --full
#
# Default (speed — browser CDN path only; no freestanding / no PXE push):
#   ./scripts/dev-up.sh               # µPy → docker → seed packs
#   ./scripts/dev-up.sh --seed-only   # packs into already-running container
#   ./scripts/dev-up.sh --no-upy      # skip Emscripten rebuild (reuse synced assets)
#   ./scripts/dev-up.sh --no-seed     # docker only
#   ./scripts/dev-up.sh --reseed      # wipe named volume metal-cdn-data, then docker+seed
#
# Freestanding / unix host / PXE (opt-in; slow builds):
#   ./scripts/dev-up.sh --firmware              # arch BIOS+UEFI (+ wasm) → CDN
#   ./scripts/dev-up.sh --unix                  # metal.unix.x86_64 (+ x86 if cross) → CDN
#   ./scripts/dev-up.sh --unix-only             # unix publish only (CDN must be up)
#   ./scripts/dev-up.sh --bootserver            # also push metal.ipxe (+ NBPs) via upload-bootserver
#   ./scripts/dev-up.sh --firmware --bootserver # CDN image then PXE config (common lab combo)
#   ./scripts/dev-up.sh --firmware-only         # build+publish firmware only (CDN must be up)
#   ./scripts/dev-up.sh --bootserver-only       # upload-bootserver only
#
# Auth (public-test defaults):
#   ./scripts/ensure-secrets.sh      # once → .secrets/cdn.env (gitignored)
#   docker gets REQUIRE_AUTH=true + bootstrap admin; seed uses Bearer from .secrets/token
#
# Env overrides:
#   MICROPYTHON      vanilla upstream MicroPython (default: sibling ../micropython)
#   METALPYTHON_WM   metalpython-wasmmod → engine mpwm (sibling ../metalpython-wasmmod)
#   METALPYTHON      metalpython product → engine mp (sibling ../metalpython)
# CDN URL roles (do not conflate):
#   METAL_CDN_URL       where *this script* publishes (lab docker default :8000)
#   METAL_PXE_CDN_URL   what PXE clients fetch (master default = official realm)
#                       https://cdn.pymergetic.com/cdn — never bake 127.0.0.1 into metal.ipxe
#   Kernel home bake    METAL_CDN_URL at *make* time on metal port (master = official)
# Own realm / lab seat: override the make-time METAL_CDN_URL and/or METAL_PXE_CDN_URL.
# Trees are discovered as *siblings of this repo* or via env — never os-sdk/packages/.
#   METAL_CDN_IMAGE metal-cdn
#   METAL_CDN_NAME  metal-cdn
#   METAL_CDN_PORT  8000
#   METAL_CDN_VOLUME metal-cdn-data   # docker volume wiped by --reseed only
#   METAL_CDN_REQUIRE_SIGNED  off|present|verify (default: present)
#   METAL_CDN_REQUIRE_AUTH    default true for this script
#   METAL_PXE_* / METAL_BOOT_IMAGE_URL — passed through to upload-bootserver
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
DO_FIRMWARE=0
DO_UNIX=0
DO_BOOTSERVER=0
SEED_ONLY=0

usage() {
  # Header comment block only (stop at first non-# line after shebang).
  awk 'NR==1{next} /^#/{sub(/^# ?/,""); print; next} {exit}' "$0"
  exit "${1:-0}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage 0 ;;
    --no-upy) DO_UPY=0 ;;
    --no-seed) DO_SEED=0 ;;
    --no-docker) DO_DOCKER=0 ;;
    --no-firmware) DO_FIRMWARE=0 ;;
    --no-unix) DO_UNIX=0 ;;
    --no-bootserver) DO_BOOTSERVER=0 ;;
    --firmware) DO_FIRMWARE=1 ;;
    --unix) DO_UNIX=1 ;;
    --bootserver) DO_BOOTSERVER=1 ;;
    --full|--all)
      DO_UPY=1; DO_DOCKER=1; DO_SEED=1; DO_FIRMWARE=1; DO_UNIX=1; DO_BOOTSERVER=1; SEED_ONLY=0
      ;;
    --seed-only)
      # Packs only; combine with --firmware / --bootserver if you want those too.
      SEED_ONLY=1; DO_UPY=0; DO_DOCKER=0; DO_SEED=1; DO_RESEED=0
      ;;
    --firmware-only)
      SEED_ONLY=0; DO_UPY=0; DO_DOCKER=0; DO_SEED=0; DO_RESEED=0
      DO_FIRMWARE=1
      ;;
    --unix-only)
      SEED_ONLY=0; DO_UPY=0; DO_DOCKER=0; DO_SEED=0; DO_RESEED=0
      DO_UNIX=1
      ;;
    --bootserver-only)
      SEED_ONLY=0; DO_UPY=0; DO_DOCKER=0; DO_SEED=0; DO_RESEED=0
      DO_BOOTSERVER=1
      ;;
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

_find_upy_tree() {
  # $1 = env override value (may be empty); remaining = candidate paths
  local override="$1"
  shift
  if [[ -n "$override" ]]; then
    echo "$override"
    return
  fi
  local cand
  for cand in "$@"; do
    if [[ -d "$cand/extmod/wasmmod/ports/micropython/webassembly" ]]; then
      echo "$(cd "$cand" && pwd)"
      return
    fi
  done
  return 1
}

find_micropython() {
  # Vanilla upstream — sibling of metal-cdn (or MICROPYTHON=).
  local override="${MICROPYTHON:-}" cand
  if [[ -n "$override" ]]; then
    echo "$override"
    return
  fi
  for cand in "$ROOT/../micropython" "$ROOT/../../micropython"; do
    if [[ -d "$cand/ports/webassembly" && -f "$cand/py/mpconfig.h" ]]; then
      # Reject metalpython trees that carry wasmmod.
      if [[ ! -d "$cand/extmod/wasmmod" ]]; then
        echo "$(cd "$cand" && pwd)"
        return
      fi
    fi
  done
  return 1
}

find_metalpython() {
  _find_upy_tree "${METALPYTHON:-}" \
    "$ROOT/../metalpython" \
    "$ROOT/../../metalpython"
}

find_metalpython_wm() {
  _find_upy_tree "${METALPYTHON_WM:-}" \
    "$ROOT/../metalpython-wasmmod" \
    "$ROOT/../../metalpython-wasmmod"
}

ensure_wasmmod_tools_python() {
  # tools/wasmmod.py imports pymergetic.wasmmod.tools (nested dev/tools).
  # Prefer editable install in metal-cdn .venv; else PYTHONPATH the src tree.
  local mp="$1" tools_src tools_py
  tools_src="$mp/extmod/wasmmod/dev/tools/src"
  tools_py="$ROOT/.venv/bin/python"
  if [[ -x "$tools_py" ]]; then
    if ! "$tools_py" -c 'import pymergetic.wasmmod.tools' >/dev/null 2>&1; then
      echo "==> pip install -e wasmmod dev/tools → $tools_py"
      "$tools_py" -m pip install -e "$mp/extmod/wasmmod/dev/tools" -q
    fi
    # Put venv first so make's python3 hits the installed tools.
    PATH="$(dirname "$tools_py"):$PATH"
    export PATH
  elif [[ -d "$tools_src" ]]; then
    export PYTHONPATH="${tools_src}${PYTHONPATH:+:$PYTHONPATH}"
  else
    echo "FAIL: no wasmmod tools at $mp/extmod/wasmmod/dev/tools (submodule init?)" >&2
    exit 1
  fi
}

build_and_sync_engine() {
  # $1 = tree root; $2 = engine id (mp|mpwm|upy)
  local tree="$1" engine="$2" build
  ensure_emsdk
  echo "==> make submodules (ports/webassembly) [$engine]  ($tree)"
  make -C "$tree/ports/webassembly" submodules
  make -C "$tree/mpy-cross" -j"$(nproc 2>/dev/null || echo 4)"

  if [[ "$engine" == "upy" ]]; then
    # Vanilla upstream MicroPython — NOT metalpython / NOT wasmmod.
    build="$tree/ports/webassembly/build-standard"
    echo "==> build browser VANILLA µPy [$engine]  ($tree)"
    make -C "$tree/ports/webassembly" VARIANT=standard BUILD=build-standard \
      -j"$(nproc 2>/dev/null || echo 4)"
  elif [[ "$engine" == "mp" ]]; then
    # metalpython product seat: wasmmod + frozen pymergetic.metal.arch.*
    build="$tree/ports/webassembly/build-metal"
    echo "==> build browser metal arch.wasm seat [$engine]  ($tree)"
    ensure_wasmmod_tools_python "$tree"
    make -C "$tree/extmod/metal/port/webassembly" \
      -j"$(nproc 2>/dev/null || echo 4)"
  else
    # mpwm: upy + wasmmod only (no metal arch seat)
    build="$tree/ports/webassembly/build-wasmmod"
    echo "==> build browser µPy+wasmmod [$engine]  ($tree)"
    ensure_wasmmod_tools_python "$tree"
    make -C "$tree/extmod/wasmmod/ports/micropython/webassembly" \
      -j"$(nproc 2>/dev/null || echo 4)"
  fi
  echo "==> sync REPL assets [$engine]"
  "$ROOT/scripts/sync-repl-assets.sh" "$build" "$engine"
}

ensure_emsdk() {
  if command -v emcc >/dev/null 2>&1; then
    return 0
  fi
  local envf
  for envf in "${EMSDK:+$EMSDK/emsdk_env.sh}" "$HOME/emsdk/emsdk_env.sh"; do
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
  local vanilla mp_wm mp any=0
  # Three DIFFERENT trees / roles (siblings of metal-cdn, or env):
  #   mp    = ../metalpython           (metal arch.wasm seat + wasmmod)
  #   mpwm  = ../metalpython-wasmmod   (upy + wasmmod only)
  #   upy   = ../micropython           (vanilla upstream)
  if mp="$(find_metalpython)"; then
    build_and_sync_engine "$mp" mp
    any=1
  else
    echo "note: metalpython not found (set METALPYTHON=… or sibling ../metalpython) — skip mp"
  fi
  if mp_wm="$(find_metalpython_wm)"; then
    build_and_sync_engine "$mp_wm" mpwm
    any=1
  else
    echo "note: metalpython-wasmmod not found (set METALPYTHON_WM=… or sibling ../metalpython-wasmmod) — skip mpwm"
  fi
  if vanilla="$(find_micropython)"; then
    build_and_sync_engine "$vanilla" upy
    any=1
  else
    echo "note: vanilla micropython not found (set MICROPYTHON=… or sibling ../micropython) — skip upy"
  fi
  if [[ "$any" -eq 0 ]]; then
    echo "FAIL: no engine trees found (set MICROPYTHON / METALPYTHON_WM / METALPYTHON, or clone siblings next to metal-cdn)" >&2
    exit 1
  fi
  local flat="$ROOT/pymergetic/metal/cdn/web/static/repl"
  if [[ ! -f "$flat/micropython.mjs" && -f "$flat/mp/micropython.mjs" ]]; then
    cp -f "$flat/mp/"micropython.mjs "$flat/mp/"micropython.wasm "$flat/" 2>/dev/null || true
  elif [[ ! -f "$flat/micropython.mjs" && -f "$flat/mpwm/micropython.mjs" ]]; then
    cp -f "$flat/mpwm/"micropython.mjs "$flat/mpwm/"micropython.wasm "$flat/" 2>/dev/null || true
  fi
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
  echo "==> sync metal Inspect contract (Py + www) for Docker"
  "$ROOT/scripts/sync-metal-inspect.sh"
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
  for i in $(seq 1 30); do
    if curl -sf "$CDN_URL/health" >/dev/null 2>&1; then
      echo "    healthy (${i}s)"
      echo "==> seed API token"
      mint_or_load_token
      echo "    login: ${METAL_CDN_BOOTSTRAP_ADMIN_EMAIL} (password in $SECRETS_ENV)"
      return 0
    fi
    # Container crash → fail immediately (do not burn the full wait).
    if ! docker inspect -f '{{.State.Running}}' "$NAME" 2>/dev/null | grep -qx true; then
      echo "FAIL: container $NAME not running (exit $(docker inspect -f '{{.State.ExitCode}}' "$NAME" 2>/dev/null || echo '?'))" >&2
      docker logs "$NAME" 2>&1 | tail -60 >&2 || true
      exit 1
    fi
    sleep 0.5
  done
  echo "FAIL: CDN not healthy — docker logs $NAME" >&2
  docker logs "$NAME" 2>&1 | tail -60 >&2 || true
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
  ensure_wasmmod_tools_python "$mp"
  # Prefer system cmake over broken emsdk shims; venv python already first via ensure_*.
  PATH="$(dirname "$(command -v python3)"):/usr/bin:/bin:${PATH}" make -C "$examples" sign-packs
  echo "==> publish samples → $CDN_URL (Bearer)"
  local EX=pymergetic.wasmmod_examples
  [[ -f "$packs/${EX}.hello.wasm" ]] || { echo "FAIL: missing $packs/${EX}.hello.wasm" >&2; exit 1; }

  local hello_files=("$packs/${EX}.hello.wasm")
  [[ -f "$packs/${EX}.hello.elf" ]] && hello_files+=("$packs/${EX}.hello.elf")
  [[ -f "$packs/${EX}.hello.x86_64.elf" ]] && hello_files+=("$packs/${EX}.hello.x86_64.elf")
  [[ -f "$packs/${EX}.hello.aarch64.elf" ]] && hello_files+=("$packs/${EX}.hello.aarch64.elf")
  pub_pkg "${EX}.hello" "${hello_files[@]}"

  local client_files=("$packs/${EX}.client.wasm")
  [[ -f "$packs/${EX}.client.elf" ]] && client_files+=("$packs/${EX}.client.elf")
  [[ -f "$packs/${EX}.client.x86_64.elf" ]] && client_files+=("$packs/${EX}.client.x86_64.elf")
  pub_pkg "${EX}.client" "{\"${EX}.hello\":\"0.1.0\"}" "${client_files[@]}"

  local host_files=()
  [[ -f "$packs/${EX}.hostcall.elf" ]] && host_files+=("$packs/${EX}.hostcall.elf")
  [[ -f "$packs/${EX}.hostcall.x86_64.elf" ]] && host_files+=("$packs/${EX}.hostcall.x86_64.elf")
  if [[ ${#host_files[@]} -gt 0 ]]; then
    pub_pkg "${EX}.hostcall" "${host_files[@]}"
  fi

  local ticks_files=()
  [[ -f "$packs/${EX}.ticks.wasm" ]] && ticks_files+=("$packs/${EX}.ticks.wasm")
  [[ -f "$packs/${EX}.ticks.elf" ]] && ticks_files+=("$packs/${EX}.ticks.elf")
  if [[ ${#ticks_files[@]} -gt 0 ]]; then
    pub_pkg "${EX}.ticks" "${ticks_files[@]}"
  fi

  pub_pkg "${EX}.mixed"                "$packs/${EX}.mixed.wasm"
  pub_pkg "${EX}.bridge"               "$packs/${EX}.bridge.wasm"
  pub_pkg "${EX}.test_a"               "$packs/${EX}.test_a.wasm"
  pub_pkg "${EX}.test_a.test_d"        "$packs/${EX}.test_a.test_d.wasm"
  pub_pkg "${EX}.test_a.test_b.test_c" "{\"${EX}.test_a.test_d\":\"0.1.0\"}" "$packs/${EX}.test_a.test_b.test_c.wasm"
  pub_pkg "${EX}.test_a2"              "$packs/${EX}.test_a2.wasm"
  pub_pkg "${EX}.test_a2.test_d2"      "$packs/${EX}.test_a2.test_d2.wasm"
  pub_pkg "${EX}.test_a2.test_b2.test_c2" "{\"${EX}.test_a2.test_d2\":\"0.1.0\"}" "$packs/${EX}.test_a2.test_b2.test_c2.wasm"

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

  # Metal platform packs (kernel + Inspect face) — Play off, Inspect on.
  echo "==> build metal platform packs (wasmmod pack sections)"
  "$ROOT/scripts/build-platform-packs.sh" "$packs"
  local plat
  for plat in pymergetic.metal pymergetic.metal.inspect; do
    if [[ -f "$packs/${plat}.wasm" ]]; then
      echo "SIGN $packs/${plat}.wasm"
      PYTHONPATH="$mp/extmod/wasmmod/dev/tools/src${PYTHONPATH:+:$PYTHONPATH}" \
        python3 -m pymergetic.wasmmod.tools sign sign \
        --key "$examples/.keys/sign/leaf.key.pem" \
        --chain "$examples/.keys/sign/chain.der" \
        "$packs/${plat}.wasm"
      pub_pkg "$plat" "$packs/${plat}.wasm"
    else
      echo "WARN: missing $packs/${plat}.wasm — skip" >&2
    fi
  done

  echo "==> lead packages:"
  curl -sf "$CDN_URL/index/lead" | python3 -c \
    "import sys,json; print('   ', ', '.join(sorted(json.load(sys.stdin).get('packages',{}))))"
  echo -n "==> signature check ${EX}.hello.wasm … "
  curl -sf "$CDN_URL/artifacts/lead/${EX}.hello.wasm/inspect" | python3 -c \
    'import sys,json; d=json.load(sys.stdin); ok=bool(d.get("signed") and d.get("sig")); print("signed" if ok else "UNSIGNED"); raise SystemExit(0 if ok else 1)'
  if [[ -f "$packs/${EX}.hello.elf" ]]; then
    echo -n "==> signature check ${EX}.hello.elf … "
    curl -sf "$CDN_URL/artifacts/lead/${EX}.hello.elf/inspect" | python3 -c \
      'import sys,json; d=json.load(sys.stdin); ok=bool(d.get("signed") and d.get("sig")); print("signed" if ok else "UNSIGNED"); raise SystemExit(0 if ok else 1)'
  fi
}

sign_artifact() {
  # $1 = file; uses wasmmod examples PKI (same as step_seed).
  local f="$1" mp examples
  mp="$(find_metalpython)" || return 1
  examples="$mp/extmod/wasmmod/examples"
  ensure_wasmmod_tools_python "$mp"
  [[ -f "$examples/.keys/sign/leaf.key.pem" ]] || {
    echo "==> generate wasmmod sign PKI (examples/.keys)"
    PATH="$(dirname "$(command -v python3)"):/usr/bin:/bin:${PATH}" make -C "$examples" sign-key
  }
  echo "SIGN $f"
  PYTHONPATH="$mp/extmod/wasmmod/dev/tools/src${PYTHONPATH:+:$PYTHONPATH}" \
    python3 -m pymergetic.wasmmod.tools sign sign \
    --key "$examples/.keys/sign/leaf.key.pem" \
    --chain "$examples/.keys/sign/chain.der" \
    "$f"
}

# Publish one freestanding arch seat: BIOS .elf + UEFI .efi
# Args: PKG BOARD_BIOS BOARD_UEFI EFI_NAME (BOOTX64.EFI | BOOTIA32.EFI)
pub_arch_firmware() {
  local pkg="$1" board_bios="$2" board_uefi="$3" efi_name="$4"
  local port="$5" outdir="$6" jobs="$7"
  local bios_build uefi_build bios_src uefi_src bios_pub uefi_pub

  bios_build="$port/build/${board_bios}-mp-repl"
  echo "==> build freestanding BIOS ($board_bios ENGINE=mp REPL=1)"
  make -C "$port" BOARD="$board_bios" ENGINE=mp REPL=1 \
    BUILD="build/${board_bios}-mp-repl" -j"$jobs" all
  bios_src="$bios_build/metal.qemu.elf"
  [[ -f "$bios_src" ]] || bios_src="$bios_build/metal.elf"
  [[ -f "$bios_src" ]] || {
    echo "FAIL: missing BIOS image under $bios_build" >&2
    return 1
  }
  bios_pub="$outdir/${pkg}.elf"
  cp -f "$bios_src" "$bios_pub"
  file "$bios_pub" | grep -q 'ELF 32-bit' || {
    echo "FAIL: expected ELF32 for BIOS netboot, got: $(file "$bios_pub")" >&2
    return 1
  }
  sign_artifact "$bios_pub"

  uefi_build="$port/build/${board_uefi}-mp-repl"
  echo "==> build freestanding UEFI ($board_uefi ENGINE=mp REPL=1)"
  make -C "$port" BOARD="$board_uefi" ENGINE=mp REPL=1 \
    BUILD="build/${board_uefi}-mp-repl" -j"$jobs" all
  uefi_src="$uefi_build/esp/EFI/BOOT/${efi_name}"
  [[ -f "$uefi_src" ]] || uefi_src="$uefi_build/${efi_name}"
  [[ -f "$uefi_src" ]] || {
    echo "FAIL: missing UEFI $efi_name under $uefi_build" >&2
    return 1
  }
  uefi_pub="$outdir/${pkg}.efi"
  cp -f "$uefi_src" "$uefi_pub"
  file "$uefi_pub" | grep -Eqi 'PE32|EFI|executable' || {
    echo "note: UEFI image type: $(file "$uefi_pub")"
  }

  echo "==> publish $pkg → $CDN_URL"
  pub_pkg "$pkg" "$bios_pub" "$uefi_pub"
  echo -n "==> lead ${pkg}.elf … "
  curl -sf "$CDN_URL/artifacts/lead/${pkg}.elf" -o /dev/null \
    && echo "ok" \
    || { echo "FAIL: lead fetch elf" >&2; return 1; }
  echo -n "==> lead ${pkg}.efi … "
  curl -sf "$CDN_URL/artifacts/lead/${pkg}.efi" -o /dev/null \
    && echo "ok" \
    || { echo "FAIL: lead fetch efi" >&2; return 1; }
}

step_unix() {
  # Linux userspace seats → CDN (curl-and-run .elf; role=host).
  #   pymergetic.metal.unix.x86_64
  #   pymergetic.metal.unix.x86  (when i686-linux-gnu- cross exists)
  local mp port jobs outdir pkg build bin pub
  load_secrets
  if [[ ! -f "$SECRETS_TOKEN" ]]; then
    echo "==> seed API token (unix publish)"
    mint_or_load_token
  fi
  mp="$(find_metalpython)" || {
    echo "FAIL: metalpython not found (set METALPYTHON=…)" >&2
    exit 1
  }
  port="$mp/extmod/metal/port/unix"
  jobs="$(nproc 2>/dev/null || echo 4)"
  outdir="$mp/extmod/metal/port/build/cdn-publish"
  mkdir -p "$outdir"

  echo "==> build metal unix x86_64 host"
  make -C "$port" BUILD="$mp/ports/unix/build-metal" -j"$jobs" all
  bin="$mp/ports/unix/build-metal/micropython"
  [[ -x "$bin" ]] || {
    echo "FAIL: missing $bin" >&2
    exit 1
  }
  pkg="pymergetic.metal.unix.x86_64"
  pub="$outdir/${pkg}.elf"
  cp -f "$bin" "$pub"
  file "$pub" | grep -q 'ELF' || {
    echo "FAIL: expected ELF for unix host, got: $(file "$pub")" >&2
    exit 1
  }
  sign_artifact "$pub"
  echo "==> publish $pkg → $CDN_URL"
  pub_pkg "$pkg" "$pub"
  curl -sf "$CDN_URL/artifacts/lead/${pkg}.elf" -o /dev/null \
    && echo "    lead ${pkg}.elf ok" \
    || { echo "FAIL: lead fetch unix x86_64" >&2; exit 1; }

  # i686 seat: gnu cross if present, else clang -m32 (needs libc6-dev-i386 / multilib).
  local unix_x86_cc="" unix_x86_cflags='-UPM_METAL_CFG_ARCH_X86_64 -DPM_METAL_CFG_ARCH_X86=1'
  if command -v i686-linux-gnu-gcc >/dev/null 2>&1; then
    unix_x86_cc="i686-linux-gnu-gcc"
  elif clang -m32 -x c - -o /tmp/metal-unix-i686-probe - <<<'int main(void){return 0;}' >/dev/null 2>&1; then
    unix_x86_cc="clang -m32"
    unix_x86_cflags+=' -m32'
    rm -f /tmp/metal-unix-i686-probe
  fi
  if [[ -n "$unix_x86_cc" ]]; then
    echo "==> build metal unix x86 ($unix_x86_cc)"
    rm -rf "$mp/ports/unix/build-metal-i686"
    make -C "$port" \
      CC="$unix_x86_cc" \
      BUILD="$mp/ports/unix/build-metal-i686" \
      CFLAGS_EXTRA="$unix_x86_cflags" \
      -j"$jobs" all
    bin="$mp/ports/unix/build-metal-i686/micropython"
    [[ -x "$bin" ]] || {
      echo "FAIL: missing $bin" >&2
      exit 1
    }
    file "$bin" | grep -qE 'ELF 32-bit|Intel 80386|i386' || {
      echo "FAIL: expected 32-bit ELF for unix.x86, got: $(file "$bin")" >&2
      exit 1
    }
    pkg="pymergetic.metal.unix.x86"
    pub="$outdir/${pkg}.elf"
    cp -f "$bin" "$pub"
    sign_artifact "$pub"
    echo "==> publish $pkg → $CDN_URL"
    pub_pkg "$pkg" "$pub"
    curl -sf "$CDN_URL/artifacts/lead/${pkg}.elf" -o /dev/null \
      && echo "    lead ${pkg}.elf ok" \
      || { echo "FAIL: lead fetch unix x86" >&2; exit 1; }
  else
    echo "note: no i686 toolchain (i686-linux-gnu-gcc or clang -m32) — skip pymergetic.metal.unix.x86"
  fi
}

step_firmware() {
  # Arch seat images → CDN (same shelf; NOT bare "metal", NOT Python guests).
  #   pymergetic.metal.arch.x86_64 — BIOS ELF32 trampoline + UEFI BOOTX64
  #   pymergetic.metal.arch.x86    — i686 Multiboot ELF32 + UEFI BOOTIA32
  #   pymergetic.metal.arch.wasm   — .mjs + .wasm (CDN UI `mp` engine)
  local mp port jobs outdir
  local wasm_pkg="pymergetic.metal.arch.wasm"
  local wasm_build wasm_src mjs_src wasm_pub mjs_pub
  load_secrets
  if [[ ! -f "$SECRETS_TOKEN" ]]; then
    echo "==> seed API token (firmware publish)"
    mint_or_load_token
  fi
  mp="$(find_metalpython)" || {
    echo "FAIL: metalpython not found (set METALPYTHON=…)" >&2
    exit 1
  }
  port="$mp/extmod/metal/port"
  jobs="$(nproc 2>/dev/null || echo 4)"
  outdir="$mp/extmod/metal/port/build/cdn-publish"
  mkdir -p "$outdir"

  pub_arch_firmware pymergetic.metal.arch.x86_64 \
    X86_64_BIOS X86_64_UEFI BOOTX64.EFI "$port" "$outdir" "$jobs"
  pub_arch_firmware pymergetic.metal.arch.x86 \
    X86_BIOS X86_UEFI BOOTIA32.EFI "$port" "$outdir" "$jobs"

  # Browser arch.wasm seat → CDN (UI `mp` pill loads these lead artifacts).
  wasm_build="$mp/ports/webassembly/build-metal"
  wasm_src="$wasm_build/micropython.wasm"
  mjs_src="$wasm_build/micropython.mjs"
  if [[ ! -f "$wasm_src" || ! -f "$mjs_src" ]]; then
    echo "==> build browser metal arch.wasm seat (missing $wasm_build)"
    ensure_emsdk
    build_and_sync_engine "$mp" mp
  fi
  [[ -f "$wasm_src" && -f "$mjs_src" ]] || {
    echo "FAIL: missing $wasm_src or $mjs_src" >&2
    exit 1
  }
  wasm_pub="$outdir/${wasm_pkg}.wasm"
  mjs_pub="$outdir/${wasm_pkg}.mjs"
  cp -f "$wasm_src" "$wasm_pub"
  cp -f "$mjs_src" "$mjs_pub"
  sign_artifact "$wasm_pub"
  echo "==> publish $wasm_pkg → $CDN_URL"
  pub_pkg "$wasm_pkg" "$wasm_pub" "$mjs_pub"
  echo -n "==> lead ${wasm_pkg}.wasm … "
  curl -sf "$CDN_URL/artifacts/lead/${wasm_pkg}.wasm" -o /dev/null \
    && echo "ok" \
    || { echo "FAIL: lead fetch wasm" >&2; exit 1; }
  echo -n "==> lead ${wasm_pkg}.mjs … "
  curl -sf "$CDN_URL/artifacts/lead/${wasm_pkg}.mjs" -o /dev/null \
    && echo "ok" \
    || { echo "FAIL: lead fetch mjs" >&2; exit 1; }

  # Drop legacy bare "metal" package (mis-publish).
  yank_pkg_if_present metal "renamed to pymergetic.metal.arch.*"
}

yank_pkg_if_present() {
  local name="$1" reason="${2:-yanked}" token code
  token="$(seed_token)"
  code="$(curl -sS -o /tmp/metal-cdn-yank.json -w '%{http_code}' -X POST \
    "$CDN_URL/packages/${name}/yank" \
    -H "Authorization: Bearer $token" \
    -H 'Content-Type: application/json' \
    -d "$(python3 -c 'import json,sys; print(json.dumps({"channel":"lead","reason":sys.argv[1]}))' "$reason")")"
  if [[ "$code" == "200" || "$code" == "201" ]]; then
    echo "    yanked package $name (HTTP $code)"
  elif [[ "$code" == "404" ]]; then
    echo "    yank $name: not present (ok)"
  else
    echo "    WARN: yank $name HTTP $code — $(head -c 160 /tmp/metal-cdn-yank.json 2>/dev/null)" >&2
  fi
}

step_bootserver() {
  local mp up
  mp="$(find_metalpython)" || {
    echo "FAIL: metalpython not found (set METALPYTHON=…)" >&2
    exit 1
  }
  up="$mp/extmod/metal/deploy/upload-bootserver"
  [[ -x "$up" || -f "$up" ]] || {
    echo "FAIL: missing $up" >&2
    exit 1
  }
  # Boot server = iPXE NBP + metal.ipxe only. Images from PXE-reachable CDN
  # (NOT local docker 127.0.0.1 — PXE clients cannot reach the build host).
  export METAL_PXE_CDN_URL="${METAL_PXE_CDN_URL:-https://cdn.pymergetic.com/cdn}"
  unset METAL_CDN_URL
  echo "==> upload-bootserver → ${METAL_PXE_HOST:-192.168.10.1}:${METAL_PXE_PATH:-/storage/tftp}"
  echo "    boot server = iPXE + cfg; images from METAL_PXE_CDN_URL=$METAL_PXE_CDN_URL"
  bash "$up"
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
  if [[ ! -f "$ROOT/pymergetic/metal/cdn/web/static/repl/micropython.mjs" \
     && ! -f "$ROOT/pymergetic/metal/cdn/web/static/repl/mpwm/micropython.mjs" \
     && ! -f "$ROOT/pymergetic/metal/cdn/web/static/repl/mp/micropython.mjs" \
     && ! -f "$ROOT/pymergetic/metal/cdn/web/static/repl/upy/micropython.mjs" ]]; then
    echo "note: no synced REPL assets — building µPy first"
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

if [[ "$DO_FIRMWARE" -eq 1 ]]; then
  if ! curl -sf "$CDN_URL/health" >/dev/null 2>&1; then
    echo "FAIL: CDN not reachable at $CDN_URL (need docker up before --firmware)" >&2
    exit 1
  fi
  step_firmware
fi

if [[ "$DO_UNIX" -eq 1 ]]; then
  if ! curl -sf "$CDN_URL/health" >/dev/null 2>&1; then
    echo "FAIL: CDN not reachable at $CDN_URL (need docker up before --unix)" >&2
    exit 1
  fi
  step_unix
fi

if [[ "$DO_BOOTSERVER" -eq 1 ]]; then
  step_bootserver
fi

ok_lead() {
  # Print "    <label> <url>" only if the lead artifact is fetchable.
  local label="$1" name="$2" url
  url="$CDN_URL/artifacts/lead/$name"
  if curl -sf "$url" -o /dev/null; then
    printf '    %-6s %s\n' "$label" "$url"
    return 0
  fi
  return 1
}

echo
echo "OK  UI     $CDN_URL/"
if [[ "$DO_FIRMWARE" -eq 1 ]]; then
  ok_lead fw "pymergetic.metal.arch.x86_64.elf" || true
  ok_lead "" "pymergetic.metal.arch.x86_64.efi" || true
  ok_lead "" "pymergetic.metal.arch.x86.elf" || true
  ok_lead "" "pymergetic.metal.arch.x86.efi" || true
  ok_lead "" "pymergetic.metal.arch.wasm.wasm" || true
  ok_lead "" "pymergetic.metal.arch.wasm.mjs" || true
fi
if [[ "$DO_UNIX" -eq 1 ]]; then
  ok_lead unix "pymergetic.metal.unix.x86_64.elf" || true
  ok_lead "" "pymergetic.metal.unix.x86.elf" || true
fi
if [[ "$DO_BOOTSERVER" -eq 1 ]]; then
  pxe_cdn="${METAL_PXE_CDN_URL:-https://cdn.pymergetic.com/cdn}"
  echo "    pxe    ${METAL_PXE_HOST:-192.168.10.1} = iPXE NBP + metal.ipxe only"
  echo "           images ← ${pxe_cdn}/artifacts/lead/pymergetic.metal.arch.*"
fi
echo "    auth   REQUIRE_AUTH on — login ${METAL_CDN_BOOTSTRAP_ADMIN_EMAIL:-demo@…}"
echo "    secrets $SECRETS_ENV  |  token $SECRETS_TOKEN"
echo "    shell  open µPy panel → packages()  |  import pymergetic.wasmmod_examples.hello"
echo "    stop   docker rm -f $NAME"
