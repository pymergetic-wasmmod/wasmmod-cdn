#!/usr/bin/env bash
# Clean-room smoke: PyPI metal-cdn (+ client) in /tmp, then wasmmod client floor.
#
#   ./scripts/smoke-pypi-tmp.sh
#   KEEP=1 ./scripts/smoke-pypi-tmp.sh   # leave workdir + server running
#
# Env:
#   METAL_CDN_PORT   default 18080 (avoid clashing with a local :8000 demo)
#   WASMMOD          path to wasmmod checkout (default: sibling metalpython tree)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${METAL_CDN_PORT:-18080}"
CDN_URL="http://127.0.0.1:${PORT}/cdn"
KEEP="${KEEP:-0}"
EXPECT_VER="${EXPECT_VER:-0.1.0a5}"

find_wasmmod() {
  if [[ -n "${WASMMOD:-}" ]]; then
    echo "$WASMMOD"
    return
  fi
  local cand
  for cand in \
    "$ROOT/../metalpython/extmod/wasmmod" \
    "$HOME/Devel/os-sdk/packages/metalpython/extmod/wasmmod"
  do
    if [[ -f "$cand/requirements-publish.txt" ]]; then
      echo "$(cd "$cand" && pwd)"
      return
    fi
  done
  return 1
}

WASMMOD_DIR="$(find_wasmmod)" || {
  echo "FAIL: wasmmod checkout not found (set WASMMOD=…)" >&2
  exit 1
}

WORK="$(mktemp -d /tmp/metal-cdn-pypi-smoke.XXXXXX)"
cleanup() {
  if [[ -n "${SERVER_PID:-}" ]] && kill -0 "$SERVER_PID" 2>/dev/null; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
  if [[ "$KEEP" != "1" ]]; then
    rm -rf "$WORK"
  else
    echo "KEEP=1 → left $WORK (server pid ${SERVER_PID:-none})"
  fi
}
trap cleanup EXIT

echo "==> workdir $WORK"
echo "==> expect PyPI version $EXPECT_VER"
echo "==> wasmmod $WASMMOD_DIR"

python3 -m venv "$WORK/venv"
# shellcheck disable=SC1091
source "$WORK/venv/bin/activate"
# Host ~/.config/pip/pip.conf adds a private extra-index that can shadow PyPI alphas.
export PIP_CONFIG_FILE="/dev/null"
export PIP_INDEX_URL="https://pypi.org/simple"
export PIP_EXTRA_INDEX_URL=""
python -m pip install -U pip -q

echo "==> pip install from PyPI (no local editable)"
python -m pip install --index-url https://pypi.org/simple \
  "pymergetic-metal-cdn-client==${EXPECT_VER}" \
  "pymergetic-metal-cdn==${EXPECT_VER}" -q

python - <<PY
from importlib.metadata import version
cv = version("pymergetic-metal-cdn-client")
sv = version("pymergetic-metal-cdn")
assert cv == "${EXPECT_VER}", cv
assert sv == "${EXPECT_VER}", sv
import pymergetic.metal.cdn_client as c
import pymergetic.metal.cdn as s
print("client", cv, "module", getattr(c, "__version__", "?"))
print("server", sv)
PY

metal-cdn --version | tee "$WORK/cli-version.txt"
grep -q "$EXPECT_VER" "$WORK/cli-version.txt"

echo "==> start metal-cdn on :$PORT"
export METAL_CDN_DATA_DIR="$WORK/data"
export METAL_CDN_STORAGE_ROOT="$WORK/data/packs"
export METAL_CDN_DATABASE_URL="sqlite+aiosqlite:///$WORK/data/metal_cdn.db"
export METAL_CDN_BASE_PATH="/cdn"
export METAL_CDN_HOST="127.0.0.1"
export METAL_CDN_PORT="$PORT"
export METAL_CDN_EXPERIMENTAL=true
export METAL_CDN_REQUIRE_AUTH=false
export METAL_CDN_SESSION_SECRET="smoke-test-secret"
mkdir -p "$METAL_CDN_STORAGE_ROOT"

metal-cdn serve >"$WORK/server.log" 2>&1 &
SERVER_PID=$!

for i in $(seq 1 40); do
  if curl -sf "$CDN_URL/health" >/dev/null; then
    break
  fi
  sleep 0.25
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "FAIL: server died" >&2
    tail -50 "$WORK/server.log" >&2
    exit 1
  fi
  if [[ "$i" -eq 40 ]]; then
    echo "FAIL: health timeout" >&2
    tail -50 "$WORK/server.log" >&2
    exit 1
  fi
done

echo "==> health / status"
curl -sf "$CDN_URL/health" | tee "$WORK/health.json"
curl -sf "$CDN_URL/status" | tee "$WORK/status.json"
python - <<PY
import json
from pathlib import Path
st = json.loads(Path("$WORK/status.json").read_text())
assert st.get("experimental") is True, st
print("status ok experimental=", st.get("experimental"))
PY

echo "==> CdnClient against local server"
python - <<PY
from pymergetic.metal.cdn_client import CdnClient
c = CdnClient("$CDN_URL")
h = c.health()
print("client.health", h)
st = c.status()
print("client.status experimental", st.get("experimental"))
assert st.get("experimental") is True
PY

echo "==> wasmmod requirements-publish + require_cdn_client"
python -m pip install --index-url https://pypi.org/simple -r "$WASMMOD_DIR/requirements-publish.txt" -q
python - <<PY
import sys
sys.path.insert(0, "$WASMMOD_DIR/tools")
from wasmmod_cliutil import CLIENT_MIN_VERSION, require_cdn_client, client_version_ok
from importlib.metadata import version
inst = version("pymergetic-metal-cdn-client")
print("CLIENT_MIN_VERSION", CLIENT_MIN_VERSION)
print("installed", inst)
assert client_version_ok(inst), (inst, CLIENT_MIN_VERSION)
mod = require_cdn_client("smoke")
print("require_cdn_client ok", getattr(mod, "__version__", inst))
PY

echo "==> register + claim via CLI (isolated HOME)"
export HOME="$WORK/home"
mkdir -p "$HOME"
metal-cdn login --url "$CDN_URL" --email "smoke@example.com" --password "smoke-smoke-1" --register
metal-cdn whoami
metal-cdn claim smokehello || true
metal-cdn status

echo "PASS: PyPI $EXPECT_VER server+client, local CDN, wasmmod client floor"
