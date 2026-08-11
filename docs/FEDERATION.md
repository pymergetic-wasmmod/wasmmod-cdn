# Federation (CDN ↔ CDN)

Prefix-mounted child CDNs: a parent can mount a peer on a package-name prefix
(`a`, `a.b`, …). Longest prefix wins; nested mounts on the child are allowed.
**Read proxy** (seamless catalog/artifacts) lands after the control plane below.

## Threat model (short)

| Trust | Role |
|-------|------|
| **Pack signatures (MPWS)** | End devices still verify artifacts. Federation does **not** replace pack trust. |
| **Federation credentials** | Server→server only. Parent stores an encrypted bearer (or later a signing key); child issues a scoped API key (`federation:read`) via **Accept grant**. |
| **Browser sessions** | Never forwarded to peers. Humans stay on one origin; “Visit on remote CDN” is an explicit egress link. |
| **SSRF** | Peer URLs must be `http(s)`. Private/link-local peers require `WASMMOD_CDN_FEDERATION_ALLOW_PRIVATE_NET=1` (lab). Enforced when the proxy ships. |
| **Hops / cycles** | `WASMMOD_CDN_FEDERATION_MAX_HOPS` (default 8) + hop/trace headers on proxy requests. |
| **Shadowing** | Default: **local wins** over the same FQN on a child. |
| **Private packs** | Only if the child grant’s bot user has ACL; do not leak private index rows through public merge. |

Attackers who obtain a federation bearer can **read** (and later maybe publish) whatever that grant allows on the child — treat tokens like deploy secrets; rotate via admin credential PUT / grant revoke.

## Naming

Mount prefixes use the same grammar as package names (`ChannelLayout.validate_package_name`):
dotted `a.b.c` or legacy `org/pkg`. A mount on `a.b` covers `a.b` and `a.b.*`.

## Control plane (implemented)

Admin (Bearer admin or session admin):

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/admin/federation/status` | Counts; `proxy_ready` flag |
| CRUD | `/admin/federation/peers` | Remote base URL + label |
| CRUD | `/admin/federation/mounts` | Prefix → peer; optional `bearer_token` at create |
| PUT | `/admin/federation/mounts/{id}/credential` | Rotate encrypted bearer |
| POST | `/admin/federation/grants/accept` | **Child**: mint scoped key + grant (one-time `api_key` in response) |
| POST | `/admin/federation/grants/{id}/revoke` | Revoke grant + API key |
| GET | `/federation/mounts` | Public: enabled prefixes + browse URLs (no secrets) |

Audit actions: `fed.peer.*`, `fed.mount.*`, `fed.credential.rotate`, `fed.grant.*`.

### Link flow

1. **Child** admin: `POST …/grants/accept` with `prefix`, `parent_label` → copy `api_key`.
2. **Parent** admin: create peer (`base_url` = child’s public CDN root), create mount with that `prefix` + `bearer_token` = child’s key.
3. Devices keep using the **parent** `WASMMOD_CDN_URL` / `wasm.cdn(parent)` once the read proxy exists.

### Settings

```sh
# Optional dedicated key for Fernet; else session_secret
WASMMOD_CDN_FEDERATION_SECRETS_KEY=
WASMMOD_CDN_FEDERATION_MAX_HOPS=8
WASMMOD_CDN_FEDERATION_ALLOW_PRIVATE_NET=false
# Bootstrap hint — applied idempotently on startup (existing prefixes win):
# WASMMOD_CDN_FEDERATION_MOUNTS_JSON=[{"prefix":"a.b","url":"https://leaf/cdn","token":"mcdn_…"}]
# Lab private peers also need WASMMOD_CDN_FEDERATION_ALLOW_PRIVATE_NET=1
```

API keys gain a `scopes` column. Empty scopes = unrestricted (legacy human/CLI keys). Federation bot keys use `["federation:read"]`.

Scoped Bearer keys are enforced in `get_optional_user`:

| Scope | Allowed |
|-------|---------|
| `federation:read` | GET/HEAD health, `/auth/me`, `/federation/mounts`, `/packages*`, `/artifacts/*`, `/index/*` |
| `federation:publish` | Same reads, plus `POST /publish` |
| anything else (admin, claim, …) | **403** for scoped keys |

Unscoped keys and session cookies are unchanged. Artifact routes that skip auth deps remain public as before.

## Data plane (read proxy — implemented)

On local miss (package / versions / artifact GET|HEAD):

1. Longest-prefix mount for the package name (artifacts: derive FQN from filename).
2. Forward to peer with optional encrypted bearer + `X-Metal-Fed-Hop` / Trace.
3. Response may include `X-Metal-Origin: remote` and `X-Metal-Fed-Mount: <prefix>`.
4. Local package/artifact of the same name **shadows** the peer (no remote headers).

### Ticket auth (Ed25519)

Parent can install an Ed25519 key on a mount (`POST /admin/federation/mounts/{id}/fed-key`) and paste the
**public** key into the child’s grant (`parent_public_key` on accept). Proxy then sends
`Authorization: MetalFed <payload>.<sig>` instead of a long-lived Bearer when a fed key is present.
Child verifies against active grants; scopes come from the ticket (`federation:read` / `federation:publish`).
Bearer grants remain supported.

### Publish upstream (foothold)

Parent mount with `direction=push`; child grant with `allow_publish` (adds `federation:publish`). Then:

```http
POST /publish
Content-Type: multipart/form-data

meta=…&upstream=true&files=…
# optional dual-write:
meta=…&upstream=true&also_local=true&files=…
```

Parent ACL still applies. Default `upstream=true` writes **only** on the peer; `also_local=true`
mirrors locally too (`X-Metal-Fed-Dual-Write: 1`). Peer must allow the federation bot:
unclaimed + `auto_claim_on_publish`, **or** an active grant prefix covering the package
(bot may publish claimed packs under its grant). May set `X-Metal-Origin: remote`.

CLI: `wasmmod-cdn publish pkg 1.0.0 ./pkg.wasm --upstream`  
Dual-write: `… --upstream --also-local`

### Catalog polish

- `GET /packages?prefix=a.b` filters local + federated rows (and is forwarded to peers).
- Short TTL **negative cache** on peer 404s (avoids hot miss fan-out).

### Catalog + UI (P2)

- Merged browse catalog / `GET /packages?channel=lead` with `origin=remote`, mount + peer browse URL.
- Sidebar nav rebuilds from the merged catalog; remote nodes tinted.
- Flags: **remote** pill, **Visit remote** link, origin filter; package page banner.
- Inspect subpaths (`/files`, `/symbols`, `/disasm`, …) load via the same federation-aware artifact path as `/inspect`.

### Admin UI (P3)

Browse **Federation** (admin nav) at `/federation`:

- Status counts + `proxy_ready`
- **Child**: accept grant → one-time API key reveal
- **Parent**: create peer + mount with bearer in one step
- List / delete peers & mounts; revoke grants

Optional env bootstrap: ``WASMMOD_CDN_FEDERATION_MOUNTS_JSON`` is applied on startup
(idempotent — existing prefixes are left alone).

## UI

Federated catalog rows use remote tint + **Visit remote** (`peer_browse_url`).
Admin link flow is the `/federation` page above (API under `/admin/federation/*` remains available for scripts).
