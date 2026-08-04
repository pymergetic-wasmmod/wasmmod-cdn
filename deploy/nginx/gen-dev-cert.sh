#!/usr/bin/env bash
# Generate a short-lived self-signed cert so nginx can bind :8443 before LE.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
DIR="$ROOT/certs"
mkdir -p "$DIR"
if [[ -f "$DIR/dev.crt" && -f "$DIR/dev.key" ]]; then
  echo "dev cert already present: $DIR/dev.crt"
  exit 0
fi
openssl req -x509 -nodes -newkey rsa:2048 -days 825 \
  -keyout "$DIR/dev.key" \
  -out "$DIR/dev.crt" \
  -subj "/CN=localhost/O=metal-cdn-dev"
echo "wrote $DIR/dev.crt"
