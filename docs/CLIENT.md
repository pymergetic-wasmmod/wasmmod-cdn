# Shared CDN client

PyPI: **`pymergetic-metal-cdn-client`**  
Import: **`pymergetic.metal.cdn_client`**

One thin client for the server CLI, wasmmod, and CI. The FastAPI server
(`pymergetic-metal-cdn`) depends on this package; wasmmod should depend on the
**client only**.

Sources: [`pymergetic/metal/cdn_client/`](../pymergetic/metal/cdn_client/)  
Packaging: [`client/pyproject.toml`](../client/pyproject.toml) (separate PyPI dist).

```sh
pip install -e ./client --config-settings editable_mode=compat
# or with the server:
pip install -e ./client -e ".[dev]" --config-settings editable_mode=compat
```

## Auth (OAuth prepared, not built)

Stable contract: `Authorization: Bearer <token>`.

| Field | Today | Later |
|-------|--------|--------|
| `token` | API key from `POST /auth/token` | Same Bearer string |
| `token_source` | `"api_key"` | `"oidc"` when OIDC mints/refreshes tokens |

OIDC / passkeys will **not** change publish/claim client methods — they only
change how a Bearer token is obtained. No OAuth redirects or provider SDKs in
this package.

Config: `~/.config/metal-cdn/config.json`

## Surface

- Auth: register, password→API key, `me`
- Packages: claim, publish, promote, yank, list, search, get
- Artifacts: `download_artifact` (ETag / 304), `inspect_artifact_remote`,
  `list_symbols_remote`, `addr2line_remote`, `locations_remote`, `disasm_remote`,
  `list_sections`, `download_section` (`…/sections/raw`)
- Embedded files: `get_embedded_file`, `download_embedded_file` (`…/files`, `…/files/raw`),
  `mpy_disasm_remote` (`…/files/mpy-disasm`)
- Trust (admin): `list_trust`, `add_trust`, `delete_trust`
- Verify helpers: `verify_artifact`, `enforce_signed_policy` (needs `cryptography`)
- Index / closure: `get_index`, `closure`

Pack → AOT → sign → zlib stays in wasmmod; this library talks HTTP both ways.

On upload, metal-cdn inspects each `.wasm` / `.aot` / `.elf` / `.zlib` (shared
`pymergetic.metal.cdn_client.contents`) and stores a `contents` JSON object on
the package index entry (`pack_files`, `source_files`, `exports`, `signed`, …).
Host tools can call `inspect_upload` / `inspect_artifact` directly.

## Experimental / pre-live

Default **`METAL_CDN_EXPERIMENTAL=true`** advertises a wipe warning (data **will** be
wiped often — short tests only, not long-running experiments):

- UI: top banner on all browse pages
- API: `GET /status`, `GET /health`, `GET /ready` include `experimental` + message
- Tools: `metal-cdn status`, `metal-cdn publish`, `wasmmod publish` / `cdn` print the warning

Disable after go-live:

```sh
export METAL_CDN_EXPERIMENTAL=false
# optional custom copy while still on:
# export METAL_CDN_EXPERIMENTAL_MESSAGE="…"
```

Publish signature policy (server setting `METAL_CDN_REQUIRE_SIGNED`):

| Mode | Behavior |
|------|----------|
| `off` (default) | Accept unsigned |
| `present` | Require `wasmmod.sig` section |
| `verify` | Verify MPWS against admin trust roots (`POST /admin/trust`) |

**Versioning:** additive client APIs are fine without a wasmmod bump. Removals /
required-field changes need a new client release and a bump of wasmmod
`CLIENT_MIN_VERSION` (see `tools/wasmmod_cliutil.py`) plus
`requirements-publish.txt` floor (`>=…`).

Host discovery (pip-style):

```sh
# wasmmod
python3 tools/wasmmod.py cdn list
python3 tools/wasmmod.py cdn search hello
python3 tools/wasmmod.py cdn show hello
python3 tools/wasmmod.py cdn inspect hello
python3 tools/wasmmod.py cdn cat hello util/extra.py
python3 tools/wasmmod.py cdn hex hello util/extra.py
python3 tools/wasmmod.py cdn extract hello -o ./extracted
python3 tools/wasmmod.py cdn get hello -o ./packs --unwrap
python3 tools/wasmmod.py inspect packs/hello.wasm [--verify --trust root.crt]

# or metal-cdn CLI
metal-cdn list
metal-cdn inspect packs/hello.wasm
metal-cdn trust list|add|rm
metal-cdn download hello -o ./packs
```

## One-shot (wasmmod)

```sh
# in the wasmmod repo
pip install -r requirements-publish.txt
python3 tools/wasmmod.py publish examples/hello --version 0.1.0 \
  --key .keys/sign/leaf.key.pem --chain .keys/sign/chain.der \
  --cdn-url https://cdn.example/cdn --token "$METAL_CDN_TOKEN" --claim
```

CI: wasmmod `.github/workflows/publish-pack.yml` (build+sign+upload) and
metal-cdn `.github/workflows/publish-pack.yml` (reusable upload-only).

## Web upload

Browse UI: `GET {base}/publish` — multipart upload of **prebuilt** artifacts
(session + CSRF). Signing keys stay in CLI/CI.
