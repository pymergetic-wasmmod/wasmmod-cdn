# TLS reverse proxy (dev / single-host)

## Port map

| Public | Host fwd | Process | Role |
|--------|----------|---------|------|
| 80 | → 8080 | nginx | ACME HTTP-01 + redirect to HTTPS |
| 443 | → 8443 | nginx | TLS terminate, proxy to app |
| — | 8000 | uvicorn (`metal-cdn serve`) | App (HTTP, loopback) |

Dev on the build box:

```sh
# example: router / firewall / socat / iptables DNAT
# 80  -> host:8080
# 443 -> host:8443
```

## Base path

App mount is configurable:

```sh
METAL_CDN_BASE_PATH=/          # whole host is the CDN
METAL_CDN_BASE_PATH=/cdn       # later split: /cdn → metal-cdn, / → other
```

Default nginx snippets assume **`/cdn`** so Let’s Encrypt challenges stay on the
domain root (`/.well-known/…`) while the product lives under a subroute.

Nginx **must not strip** the prefix when `base_path=/cdn`:

```nginx
proxy_pass http://127.0.0.1:8000;   # request URI kept (/cdn/…)
```

Only set `METAL_CDN_ROOT_PATH=/cdn` if you deliberately strip in the proxy.

## Quick start

```sh
# 1) app
cp .env.example .env
# METAL_CDN_BASE_PATH=/cdn
# METAL_CDN_PORT=8000
metal-cdn serve --reload

# 2) cert dirs + nginx (from repo root)
mkdir -p deploy/certbot/www deploy/certbot/conf
docker compose --profile proxy up -d nginx

# 3) issue cert (replace host)
docker compose --profile proxy run --rm certbot certonly \
  --webroot -w /var/www/certbot \
  -d cdn.example.com --email you@example.com --agree-tos --no-eff-email

# 4) enable TLS server block (uncomment ssl bits in deploy/nginx/cdn.conf) and reload
docker compose --profile proxy exec nginx nginx -s reload
```

Browse: `https://cdn.example.com/cdn/` (or `/` if `BASE_PATH=/`).
