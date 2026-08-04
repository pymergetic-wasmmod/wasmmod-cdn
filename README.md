# pymergetic-metal-cdn

PyPI: **`pymergetic-metal-cdn`**  
Import: **`pymergetic.metal.cdn`** (`pymergetic` + `metal` are [PEP 420](https://peps.python.org/pep-0420/) namespace packages)

Async FastAPI CDN for [wasmmod](https://github.com/pymergetic/wasmmod) packs.

> Channel **state** (artifacts + `index.json`) lives in object storage.  
> Publisher **identity** (users + ACL) lives in SQLModel / SQLite or Postgres.  
> Devices keep verifying pack signatures via wasmmod — this service does not replace that.

Version is derived from git via [setuptools-scm](https://github.com/pypa/setuptools-scm) (`metal-cdn --version`).

## Install

```sh
pip install pymergetic-metal-cdn
# or from this repo (client first — server depends on it).
# Use editable_mode=compat so both namespace packages share one src tree
# (default setuptools editable finders otherwise shadow pymergetic.metal.cdn).
pip install -e ./client -e ".[dev]" --config-settings editable_mode=compat
```

Thin publish/download client (wasmmod / CI — no FastAPI): **`pymergetic-metal-cdn-client`**  
→ import `pymergetic.metal.cdn_client` — see [docs/CLIENT.md](docs/CLIENT.md) and [client/README.md](client/README.md).

## Quick start

```sh
cd packages/metal-cdn
python3 -m venv .venv && source .venv/bin/activate
pip install -e ./client -e ".[dev]" --config-settings editable_mode=compat
cp .env.example .env
metal-cdn serve --reload
# → http://0.0.0.0:8000/cdn/        UI (default BASE_PATH=/cdn)
# → http://127.0.0.1:8000/cdn/docs  OpenAPI
```

TLS edge (host forwards **80→8080**, **443→8443**): see [docs/PROXY.md](docs/PROXY.md).

## Client CLI

```sh
metal-cdn login --url http://127.0.0.1:8000/cdn --email you@example.com --register
# or CI: metal-cdn login --url … --token "$METAL_CDN_TOKEN"
metal-cdn claim hello
metal-cdn publish hello 0.1.0 ./hello.wasm ./hello.wasm.zlib
metal-cdn whoami
metal-cdn logout
```

Web UI publish (prebuilt artifacts): `{BASE_PATH}/publish` after login.

One-shot pack→AOT→sign→zlib→upload: `wasmmod.py publish` (see [docs/CLIENT.md](docs/CLIENT.md)).

Releases / tags: [docs/RELEASE.md](docs/RELEASE.md).

Stores API key in `~/.config/metal-cdn/config.json`.  
Console scripts: `metal-cdn` and `pymergetic-metal-cdn` (same entry).

Optional Postgres:

```sh
docker compose up -d db
# METAL_CDN_DATABASE_URL=postgresql+asyncpg://metal:metal@127.0.0.1:5432/metal_cdn
```

## Layout

```text
client/pyproject.toml          # pymergetic-metal-cdn-client (thin HTTP wheel)
pymergetic/                    # namespace (pkgutil.extend_path)
  metal/                       # namespace
    cdn_client/                # shared client sources (client dist)
    cdn/                       # pymergetic-metal-cdn (server)
      main.py                  FastAPI app factory + lifespan
      cli.py                   serve + login/publish (uses cdn_client)
      client.py                compat re-export of cdn_client
      …
```

```python
from pymergetic.metal.cdn.main import create_app
from pymergetic.metal.cdn_client import CdnClient
```

## API sketch

| Method | Path | Role |
|--------|------|------|
| GET | `/health` / `/ready` / `/metrics` | Liveness / readiness / Prometheus |
| POST | `/auth/*` | Register, login, token, API keys |
| POST | `/packages/{name}/claim` | Claim ownership (`org/pkg` ok) |
| GET | `/index/lead` | Device-facing `index.json` |
| GET | `/packages/{name}/closure` | Exact-deps install order |
| POST | `/publish` | Multipart publish |
| POST | `/publish/presign` | Presigned upload URLs (S3/local) |
| GET | `/artifacts/…` | Download blobs |
| POST | `/admin/gc` | Orphan blob GC (admin) |

```sh
metal-cdn db upgrade   # Alembic
```

## Related

- Pack format: [wasmmod docs/PACK.md](https://github.com/pymergetic/wasmmod/blob/main/docs/PACK.md)
- Channel docs: [docs/LAYOUT.md](docs/LAYOUT.md), [docs/INDEX.md](docs/INDEX.md)
- TLS / base_path: [docs/PROXY.md](docs/PROXY.md)
- Client package: [docs/CLIENT.md](docs/CLIENT.md)
- Roadmap: [docs/ROADMAP.md](docs/ROADMAP.md)

## License

MIT — Rouven Raudzus (`raudzus@pymergetic.com`)
