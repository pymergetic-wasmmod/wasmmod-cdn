# Index schema (proposed)

Static `index.json` beside artifacts under `packs/` or `packs/@<version>/`.

Version `1` — exact deps only; no ranges.

```json
{
  "schema": 1,
  "channel": "lead",
  "generated": "2026-08-03T00:00:00Z",
  "packages": {
    "hello": {
      "version": "0.1.0",
      "aot_version": 6,
      "deps": {
        "mixed": "0.1.0"
      },
      "artifacts": [
        {
          "path": "hello.wasm.zlib",
          "kind": "wasm",
          "encoding": "mpzl",
          "sha256": "…",
          "size": 12345
        },
        {
          "path": "hello.x86_64.aot6.zlib",
          "kind": "aot",
          "arch": "x86_64",
          "aot_version": 6,
          "encoding": "mpzl",
          "sha256": "…",
          "size": 23456
        },
        {
          "path": "hello.elf.zlib",
          "kind": "elf",
          "encoding": "mpzl",
          "sha256": "…",
          "size": 4096
        }
      ]
    }
  }
}
```

## Field notes

| Field | Meaning |
|-------|---------|
| `schema` | Index format version (bump on breaking changes) |
| `channel` | `"lead"` or `"@0.1.0"` (pin id) |
| `packages.<name>.version` | Semver string for this entry |
| `aot_version` | WAMR AOT file-format N (`wasm.AOT_VERSION`) |
| `deps` | Map of pack name → **exact** version string |
| `artifacts[].path` | Relative to the directory that holds this index |
| `artifacts[].kind` | `wasm` \| `aot` \| `elf` |
| `artifacts[].arch` | Optional host arch infix (`x86_64`, …) |
| `artifacts[].encoding` | omit / `raw` \| `mpzl` (`.zlib`) |
| `sha256` | Hex digest of the **file bytes as served** (wrapped if MPZL) |

## Lead vs pin

- **Pin index** (`packs/@0.1.0/index.json`): only packages at that version;
  artifact paths stay relative to the pin dir.
- **Lead index** (`packs/index.json`): current stable versions; may duplicate
  artifact files in `packs/` or point at pin-relative URLs later. First cut:
  duplicate files in lead (same names as pin).

## Additive fields (still schema 1)

| Field | Meaning |
|-------|---------|
| `packages.*.yanked` / `yank_reason` | Tombstone |
| `packages.*.deprecated` / `successor` | Soft redirect (`name` or `name@version`) |
| `signature` | Optional HMAC-SHA256 of canonical JSON (excl. `signature`) |

## Device fetch

- `GET /index/lead` and `GET /index/pin/{version}` — raw `index.json` (+ ETag)
- Roots: `name` (lead) or `name@version` (pin); install order via `GET /packages/{name}/closure`
- Devices still verify **pack** signatures; index HMAC is optional integrity for the catalog

## Non-goals (still)

- Dep version ranges / solvers
