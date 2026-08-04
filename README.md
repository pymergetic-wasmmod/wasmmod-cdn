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

## Docker / local demo

One script: rebuild browser µPy (if needed), sync REPL assets, `docker build`/`run`, seed sample packs:

```sh
cd packages/metal-cdn
./scripts/dev-up.sh
# → http://127.0.0.1:8000/cdn/   (µPy: packages() | import hello)
```

Flags: `--no-upy` (reuse synced assets), `--no-seed`, `--seed-only` (publish into a running container).  
Env: `METALPYTHON`, `METAL_CDN_URL`, `METAL_CDN_PORT`, `METAL_CDN_SESSION_SECRET`.

The image installs the in-tree `client/` wheel before the server (not from PyPI). Persist `/data` via the `metal-cdn-data` volume. Schema is created on first boot (`create_all`); for upgrades run `metal-cdn db upgrade` against the same volume.

## Public TLS edge

Live: **`https://cdn.pymergetic.com/cdn/`** (host forwards **80→8080**, **443→8443**).

Build/run app, nginx proxy, cert paths, issue/renew: **[docs/PROXY.md](docs/PROXY.md)**.

```sh
./scripts/dev-up.sh --no-upy                 # app on :8000
docker compose --profile proxy up -d nginx   # TLS edge on :8080/:8443
curl -sf https://cdn.pymergetic.com/cdn/health
```

## Shell sessions (browser µPy)

- `GET /repl/autoexec.py` mints/reuses a `ShellSession`, embeds `SESSION_ID`, and runs `wasm.cdn` + `install_hook` + `wasm.session_id(SESSION_ID)`.
- Pack/index GETs with the session cookie are attributed as shell events (Sessions tab).
- **Login** claims prior anon sessions onto the user (anon id kept for history). **Logout** clears `user_id` and mints a fresh anon.
- Nav shows email + Logout when signed in; otherwise Login.

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
| POST | `/auth/*` | Register, login (claims anon shell sessions), token, API keys |
| GET | `/api/sessions` | List shell sessions for cookie principal |
| GET | `/api/sessions/{id}/activity` | Last-N-minute hit buckets + recent events |
| POST | `/api/sessions/events` | Best-effort REPL events (`try_package`, `import`, …) |
| GET | `/repl/autoexec.py` | Browser µPy bootstrap (`SESSION_ID`, CDN, helpers) |
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
