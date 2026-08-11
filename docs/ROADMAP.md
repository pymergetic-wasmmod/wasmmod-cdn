# Roadmap

Full product checklist. Items marked **F1–F15** are the first fundamentals
slice (auth / claim / ACL). **N1–N10** are the second slice.

## Fundamentals (F1–F15)

- [x] **F1–F15** Auth, ACL, claim, blank `/`, CLI login/publish (see git history / prior section)

## Next 10 (N1–N10)

- [x] **N1** CSRF for cookie-session mutating requests (`X-CSRF-Token`, Bearer exempt)
- [x] **N2** Rate-limit login/register/token + publish
- [x] **N3** Open registration defaults closed when `require_auth=true`
- [x] **N4** `POST /packages/{name}/promote` (pin → lead)
- [x] **N5** `POST /packages/{name}/yank` (tombstone in index)
- [x] **N6** Pin immutability (`force=true` to overwrite)
- [x] **N7** `/health` (liveness) vs `/ready` (db + storage)
- [x] **N8** ETag / Cache-Control on artifacts (+ 304)
- [x] **N9** App `Dockerfile`
- [x] **N10** Package metadata + `GET /packages/search`

## Remaining (keep in mind)

### Auth hardening
- [ ] OIDC (GitHub/GitLab)
- [ ] Passkeys / magic-link

### ACL / packages
- [x] Scoped names (`org/pkg`)
- [x] Org/team ACL
- [x] Private packages (`PackageMeta.visibility`)
- [x] Audit log

### Publish client
- [x] Thin shared `pymergetic-wasmmod-cdn-client` (`pymergetic.wasmmod.cdn_client`)
- [x] Auth prepared for OAuth (`token_source`; Bearer-only contract)
- [x] pack → AOT → sign → zlib → upload one-shot (`wasmmod.py publish`)
- [x] GitHub Action (wasmmod-cdn reusable upload + wasmmod one-shot workflow)
- [x] Web upload UI (`GET /publish`)
- [x] Admin trust roots + `WASMMOD_CDN_REQUIRE_SIGNED` (`off`/`present`/`verify`)
- [x] Embedded `files/raw` + package viewer (hljs / hex) + CLI parity

### Channel lifecycle
- [x] Richer package metadata UI
- [x] Redirect / successor package

### Storage / edge
- [x] S3/MinIO backend (`WASMMOD_CDN_STORAGE_BACKEND`)
- [x] Presigned upload (`POST /publish/presign`)
- [x] Blob GC (`POST /admin/gc`)

### Index / wasmmod client
- [x] Device fetches `index.json` (`GET /index/lead`, `/index/pin/{ver}`)
- [x] `name@version` pin roots (`resolve.parse_root` + closure API)
- [x] Exact deps install order (`GET /packages/{name}/closure`)
- [x] Optional signed index (`WASMMOD_CDN_INDEX_SIGNING_KEY`)

### Ops
- [x] Alembic migrations (`wasmmod-cdn db upgrade`, `alembic/`)
- [x] Structured logs / metrics (`WASMMOD_CDN_JSON_LOGS`, `GET /metrics`)
- [x] Git tags for setuptools-scm (`scripts/tag-release.sh`, `docs/RELEASE.md`)
- [x] Broader auth/ACL/claim test matrix

## Earlier scaffold

- [x] Document `packs/` + `packs/@version/` layout
- [x] `index.json` schema (exact deps)
- [x] Async FastAPI app (typed Pydantic / SQLModel)
- [x] Publish API writes lead + pin + index
- [x] Browse UI + embedded OpenAPI
- [x] Dist `pymergetic-wasmmod-cdn` / import `pymergetic.wasmmod.cdn` (PEP 420 `pymergetic.wasmmod`)
