# metal-cdn

PyPI: **`pymergetic-metal-cdn`** · Client: **`pymergetic-metal-cdn-client`**  
Live demo: **[cdn.pymergetic.com/cdn](https://cdn.pymergetic.com/cdn/)** · Pack format: [wasmmod](https://github.com/pymergetic/wasmmod)

Async FastAPI channel for signed wasmmod packs — browse, publish, inspect, and try them in a browser MicroPython shell.

> **Experimental.** Data on the public demo is wiped often. Short tests only — not for production.

<p align="center">
  <img src="screenshots/browse-hello.png" alt="Package browse — hello on lead, dependents, µPy ready" width="820" />
</p>

Channel **state** (artifacts + `index.json`) lives in object storage.  
Publisher **identity** (users + ACL) lives in SQL.  
Devices still verify pack signatures with wasmmod — this service does not replace that.

---

## What you get

| Surface | What it does |
|---------|----------------|
| **Browse** | Package tree, lead / `@version` pins, dependents |
| **Artifacts** | Expand a `.wasm` / `.zlib` / AOT blob → pack file list |
| **Source viewer** | Highlighted text (py / c / rs / toml / …) or hex for binaries |
| **µPy shell** | In-browser MicroPython + wasmmod: ▶ → `import` + `exports()` |
| **Sessions** | Per-browser loader sessions — autoexec / pack / index hits |
| **Publish** | Multipart UI + CLI / thin client for CI |

<p align="center">
  <img src="screenshots/upy-shell.png" alt="Browser µPy shell — session, CDN hook, live catalog" width="820" />
</p>

---

## Inspect a pack

Click an artifact to open its guts — embedded Python (`.py` / `.mpy` / `.pyc`), natives, and optional `wasmmod.source`.

<p align="center">
  <img src="screenshots/artifacts-pack.png" alt="hello.wasm expanded — pack v3 file tree" width="820" />
</p>

Text files open with syntax highlighting; bytecode and other binaries get a hex dump.

<p align="center">
  <img src="screenshots/source-python.png" alt="Highlighted __init__.py from pack section" width="720" />
  &nbsp;
  <img src="screenshots/source-pack-toml.png" alt="Highlighted pack.toml from embedded source" width="720" />
</p>

<p align="center">
  <img src="screenshots/hex-pyc.png" alt="Hex view of a .pyc inside the pack" width="720" />
</p>

---

## Install

```sh
pip install pymergetic-metal-cdn
# from this repo (client first — server depends on it):
pip install -e ./client -e ".[dev]" --config-settings editable_mode=compat
```

Thin publish/download wheel (no FastAPI): **`pymergetic-metal-cdn-client`**  
→ `pymergetic.metal.cdn_client` — [docs/CLIENT.md](docs/CLIENT.md) · [client/README.md](client/README.md).

Version comes from git tags via [setuptools-scm](https://github.com/pypa/setuptools-scm) (`metal-cdn --version`).

---

## Quick start

```sh
cd packages/metal-cdn
python3 -m venv .venv && source .venv/bin/activate
pip install -e ./client -e ".[dev]" --config-settings editable_mode=compat
cp .env.example .env
metal-cdn serve --reload
# → http://127.0.0.1:8000/cdn/        UI
# → http://127.0.0.1:8000/cdn/docs    OpenAPI
```

### Docker one-shot

Rebuild browser µPy (if needed), sync REPL assets, `docker build`/`run`, seed sample packs:

```sh
./scripts/dev-up.sh
# → http://127.0.0.1:8000/cdn/   (µPy: packages() | import hello)
```

Flags: `--no-upy`, `--no-seed`, `--seed-only`.  
Env: `METALPYTHON`, `METAL_CDN_URL`, `METAL_CDN_PORT`, `METAL_CDN_SESSION_SECRET`.

### Public TLS edge

**https://cdn.pymergetic.com/cdn/** — see [docs/PROXY.md](docs/PROXY.md).

```sh
./scripts/dev-up.sh --no-upy
docker compose --profile proxy up -d nginx
curl -sf https://cdn.pymergetic.com/cdn/health
```

---

## Try packs in the shell

The floating **µPy** panel warms MicroPython in the background. When status is **ready**, hit ▶ on an artifact (or type it yourself):

```python
import hello
exports(hello)      # public names + types (+ docstring line when set)
hello.greet()
```

<p align="center">
  <img src="screenshots/upy-exports.png" alt="µPy shell — import hello; exports(hello)" width="720" />
</p>

```text
loaded hello · 5 public
  add (WasmFunc)
  hello (WasmFunc)
  greet (function)
  answer (function)
  util (module)
```

- `packages()` — live lead catalog (or autoexec snapshot)
- `exports(mod)` — shipped in autoexec (same place as `help()`)
- `GET /repl/autoexec.py` mints/reuses a `ShellSession`, sets `wasm.cdn` + import hook + `SESSION_ID`
- Pack/index hits with the session cookie show up under **Sessions**
- **Login** claims prior anon sessions; **Logout** mints a fresh anon

▶ on a `.wasm` / `.zlib` / AOT row runs exactly `import <pkg>` then `exports(<pkg>)`.

<p align="center">
  <img src="screenshots/sessions.png" alt="Sessions — anon principal, loader entered, pack/index/autoexec hits" width="720" />
</p>

---

## Client CLI

```sh
metal-cdn login --url http://127.0.0.1:8000/cdn --email you@example.com --register
metal-cdn claim hello
metal-cdn publish hello 0.1.0 ./hello.wasm ./hello.wasm.zlib
metal-cdn whoami
```

Web publish UI: `{BASE_PATH}/publish` after login.  
One-shot pack→AOT→sign→zlib→upload: `wasmmod.py publish` ([docs/CLIENT.md](docs/CLIENT.md)).  
Releases / PyPI tags: [docs/RELEASE.md](docs/RELEASE.md).

Optional Postgres: `docker compose up -d db` + `METAL_CDN_DATABASE_URL=…`.

---

## Layout

```text
client/pyproject.toml          # pymergetic-metal-cdn-client
pymergetic/metal/
  cdn_client/                  # thin HTTP client (shared sources)
  cdn/                         # FastAPI server + UI + REPL
screenshots/                   # UI stills for this README
```

```python
from pymergetic.metal.cdn.main import create_app
from pymergetic.metal.cdn_client import CdnClient
```

## API sketch

| Method | Path | Role |
|--------|------|------|
| GET | `/health` `/ready` `/metrics` | Liveness / readiness / Prometheus |
| POST | `/auth/*` | Register, login, token, API keys |
| GET | `/api/sessions` | Shell sessions for cookie principal |
| GET | `/repl/autoexec.py` | Browser µPy bootstrap |
| POST | `/packages/{name}/claim` | Claim ownership |
| GET | `/index/lead` | Device-facing `index.json` |
| GET | `/packages/{name}/closure` | Exact-deps install order |
| POST | `/publish` | Multipart publish |
| GET | `/artifacts/…` | Download / inspect / embedded files |

```sh
metal-cdn db upgrade   # Alembic
```

## Related

- [docs/LAYOUT.md](docs/LAYOUT.md) · [docs/INDEX.md](docs/INDEX.md) · [docs/PROXY.md](docs/PROXY.md)
- [docs/CLIENT.md](docs/CLIENT.md) · [docs/RELEASE.md](docs/RELEASE.md) · [docs/ROADMAP.md](docs/ROADMAP.md)
- Pack format: [wasmmod PACK.md](https://github.com/pymergetic/wasmmod/blob/main/docs/PACK.md)

## License

MIT — Rouven Raudzus (`raudzus@pymergetic.com`)
