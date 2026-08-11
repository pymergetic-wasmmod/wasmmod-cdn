# TLS reverse proxy (dev / single-host)

Current public edge: **`https://cdn.pymergetic.com/cdn/`**

## Port map

| Public | Host fwd | Process | Role |
|--------|----------|---------|------|
| 80 | → 8080 | nginx | ACME HTTP-01 + redirect to HTTPS |
| 443 | → 8443 | nginx | TLS terminate, proxy to app |
| — | 8000 | uvicorn (`wasmmod-cdn` container or `serve`) | App (HTTP) |

```sh
# router / firewall DNAT (already in place on this host)
# 80  -> host:8080
# 443 -> host:8443
# Do NOT publish :8443 publicly — only :443. Nginx must not put :8443 in Location
# (see absolute_redirect/port_in_redirect + explicit https://$host/… in cdn.conf).
```

## Build / run (app on :8000)

From `packages/wasmmod-cdn`:

```sh
# full local path: browser µPy → sync REPL → docker build/run → seed packs
./scripts/dev-up.sh

# faster rebuilds
./scripts/dev-up.sh --no-upy          # reuse synced micropython.mjs/wasm
./scripts/dev-up.sh --no-seed         # image only
./scripts/dev-up.sh --seed-only       # republish samples into a running container
```

Manual docker (same image `dev-up` builds):

```sh
docker build -t wasmmod-cdn .
docker rm -f wasmmod-cdn 2>/dev/null || true
docker run -d --name wasmmod-cdn -p 8000:8000 \
  -e WASMMOD_CDN_SESSION_SECRET="$(openssl rand -hex 32)" \
  -e WASMMOD_CDN_PUBLIC_ORIGIN=https://cdn.pymergetic.com \
  -e WASMMOD_CDN_BEHIND_PROXY=true \
  -e WASMMOD_CDN_EXPERIMENTAL=true \
  -e WASMMOD_CDN_EXPERIMENTAL_REPL=true \
  -v wasmmod-cdn-data:/data \
  wasmmod-cdn
```

Host / venv instead of Docker:

```sh
python3 -m venv .venv && source .venv/bin/activate
pip install -e ./client -e ".[dev]" --config-settings editable_mode=compat
cp -n .env.example .env
# ensure: BASE_PATH=/cdn, BEHIND_PROXY=true, PUBLIC_ORIGIN=https://cdn.pymergetic.com
wasmmod-cdn serve --reload   # :8000
```

Smoke:

```sh
curl -sf http://127.0.0.1:8000/cdn/health
curl -sf https://cdn.pymergetic.com/cdn/health   # via nginx + LE
```

Stop / logs:

```sh
docker logs -f wasmmod-cdn
docker rm -f wasmmod-cdn
```

## Proxy (nginx + certbot)

```sh
mkdir -p deploy/certbot/www deploy/certbot/conf
docker compose --profile proxy up -d nginx
docker compose --profile proxy ps
docker compose --profile proxy exec nginx nginx -t
docker compose --profile proxy exec nginx nginx -s reload
docker compose --profile proxy logs -f nginx
```

Nginx config: `deploy/nginx/cdn.conf` (mounted read-only). App is reached as
`http://host.docker.internal:8000` with the `/cdn` prefix kept.

## Certificate layout

Issued by Let’s Encrypt (HTTP-01) via the `certbot` compose service.

| Host path | Container path | Purpose |
|-----------|----------------|---------|
| `deploy/certbot/conf/` | `/etc/letsencrypt` | Live certs + account (root-owned) |
| `deploy/certbot/www/` | `/var/www/certbot` | ACME webroot |

```text
# SAN: cdn.pymergetic.com, pymergetic.com, www.pymergetic.com
deploy/certbot/conf/live/cdn.pymergetic.com/fullchain.pem
deploy/certbot/conf/live/cdn.pymergetic.com/privkey.pem
```

(`live/…` → symlinks into `archive/…`.) Self-signed bootstrap certs stay under
`deploy/nginx/certs/` and are unused once LE paths are set in `cdn.conf`.

`.env` / container env:

```sh
WASMMOD_CDN_PUBLIC_ORIGIN=https://cdn.pymergetic.com
WASMMOD_CDN_BEHIND_PROXY=true
WASMMOD_CDN_BASE_PATH=/cdn
```

## Base path

```sh
WASMMOD_CDN_BASE_PATH=/          # whole host is the CDN
WASMMOD_CDN_BASE_PATH=/cdn       # /cdn → wasmmod-cdn; domain root free for ACME / landing
```

Default nginx assumes **`/cdn`** so ACME stays at `/.well-known/…`. Do **not**
strip the prefix in `proxy_pass` unless you also set `WASMMOD_CDN_ROOT_PATH=/cdn`.

Devices and browser shells bind with `wasm.cdn("<origin><base_path>")` (and may
list several CDN bases). `PUBLIC_ORIGIN` + `BASE_PATH` must match what clients
actually call, or imports resolve against the wrong host.

## Branding and CORS (forks)

Upstream [pymergetic-wasmmod/wasmmod-cdn](https://github.com/pymergetic-wasmmod/wasmmod-cdn) is the
reference; forks / private mirrors keep their own name and mark:

```sh
WASMMOD_CDN_BRAND_NAME=acme-cdn
WASMMOD_CDN_BRAND_LOGO_URL=https://assets.example.com/logo.png
# or path on this CDN: /cdn/static/img/logo.png
WASMMOD_CDN_CORS_ORIGINS=["*"]   # default; empty [] = off; list for cookie cross-site
```

Static `img/*` also sends `Cross-Origin-Resource-Policy: cross-origin` so other
origins can embed your logo. CORS covers API + static for device UIs talking to
this CDN from a different page origin.

## First-time cert issue

Needs public **80→8080**. Nginx can be up on the self-signed cert first.

```sh
# RSA key — required for iPXE HTTPS PXE (ECDSA / YE2 LE defaults break iPXE TLS).
docker compose --profile proxy run --rm certbot certonly \
  --webroot -w /var/www/certbot \
  --cert-name cdn.pymergetic.com \
  -d cdn.pymergetic.com -d pymergetic.com -d www.pymergetic.com \
  --key-type rsa --rsa-key-size 2048 \
  --email raudzus@pymergetic.com \
  --agree-tos --no-eff-email --non-interactive

# Re-issue over an existing ECDSA cert:
#   … certonly … --key-type rsa --force-renewal

# ssl_certificate* in deploy/nginx/cdn.conf →
#   /etc/letsencrypt/live/cdn.pymergetic.com/{fullchain,privkey}.pem
docker compose --profile proxy exec nginx nginx -t
docker compose --profile proxy exec nginx nginx -s reload
```

iPXE-friendly TLS knobs live in `deploy/nginx/cdn.conf` (`ssl_ciphers`, `ssl_ecdh_curve`).

Browse: `https://cdn.pymergetic.com/cdn/`
(Apex / www share the same cert; `/` is the blank landing until you put something there.)

## Renew

```sh
docker compose --profile proxy run --rm certbot renew
docker compose --profile proxy exec nginx nginx -s reload
```

Monthly cron/timer recommended (~90-day lifetime).

## Day-to-day stack

| Piece | Command |
|-------|---------|
| Rebuild + restart app | `./scripts/dev-up.sh --no-upy` (or full `./scripts/dev-up.sh`) |
| Restart app only | `docker restart wasmmod-cdn` |
| Start / reload TLS edge | `docker compose --profile proxy up -d nginx` / `… exec nginx nginx -s reload` |
| Renew LE | `certbot renew` + nginx reload (above) |
| Public health | `curl -sf https://cdn.pymergetic.com/cdn/health` |
