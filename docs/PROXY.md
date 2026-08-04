# TLS reverse proxy (dev / single-host)

Current public edge: **`https://cdn.pymergetic.com/cdn/`**

## Port map

| Public | Host fwd | Process | Role |
|--------|----------|---------|------|
| 80 | → 8080 | nginx | ACME HTTP-01 + redirect to HTTPS |
| 443 | → 8443 | nginx | TLS terminate, proxy to app |
| — | 8000 | uvicorn (`metal-cdn` container or `serve`) | App (HTTP) |

```sh
# router / firewall DNAT (already in place on this host)
# 80  -> host:8080
# 443 -> host:8443
```

## Build / run (app on :8000)

From `packages/metal-cdn`:

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
docker build -t metal-cdn .
docker rm -f metal-cdn 2>/dev/null || true
docker run -d --name metal-cdn -p 8000:8000 \
  -e METAL_CDN_SESSION_SECRET="$(openssl rand -hex 32)" \
  -e METAL_CDN_PUBLIC_ORIGIN=https://cdn.pymergetic.com \
  -e METAL_CDN_BEHIND_PROXY=true \
  -e METAL_CDN_EXPERIMENTAL=true \
  -e METAL_CDN_EXPERIMENTAL_REPL=true \
  -v metal-cdn-data:/data \
  metal-cdn
```

Host / venv instead of Docker:

```sh
python3 -m venv .venv && source .venv/bin/activate
pip install -e ./client -e ".[dev]" --config-settings editable_mode=compat
cp -n .env.example .env
# ensure: BASE_PATH=/cdn, BEHIND_PROXY=true, PUBLIC_ORIGIN=https://cdn.pymergetic.com
metal-cdn serve --reload   # :8000
```

Smoke:

```sh
curl -sf http://127.0.0.1:8000/cdn/health
curl -sf https://cdn.pymergetic.com/cdn/health   # via nginx + LE
```

Stop / logs:

```sh
docker logs -f metal-cdn
docker rm -f metal-cdn
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
METAL_CDN_PUBLIC_ORIGIN=https://cdn.pymergetic.com
METAL_CDN_BEHIND_PROXY=true
METAL_CDN_BASE_PATH=/cdn
```

## Base path

```sh
METAL_CDN_BASE_PATH=/          # whole host is the CDN
METAL_CDN_BASE_PATH=/cdn       # /cdn → metal-cdn; domain root free for ACME / landing
```

Default nginx assumes **`/cdn`** so ACME stays at `/.well-known/…`. Do **not**
strip the prefix in `proxy_pass` unless you also set `METAL_CDN_ROOT_PATH=/cdn`.

## First-time cert issue

Needs public **80→8080**. Nginx can be up on the self-signed cert first.

```sh
docker compose --profile proxy run --rm certbot certonly \
  --webroot -w /var/www/certbot \
  --cert-name cdn.pymergetic.com \
  -d cdn.pymergetic.com -d pymergetic.com -d www.pymergetic.com \
  --email raudzus@pymergetic.com \
  --agree-tos --no-eff-email --non-interactive

# ssl_certificate* in deploy/nginx/cdn.conf →
#   /etc/letsencrypt/live/cdn.pymergetic.com/{fullchain,privkey}.pem
docker compose --profile proxy exec nginx nginx -t
docker compose --profile proxy exec nginx nginx -s reload
```

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
| Restart app only | `docker restart metal-cdn` |
| Start / reload TLS edge | `docker compose --profile proxy up -d nginx` / `… exec nginx nginx -s reload` |
| Renew LE | `certbot renew` + nginx reload (above) |
| Public health | `curl -sf https://cdn.pymergetic.com/cdn/health` |
