# Channel layout

Static object tree served over HTTP(S). The wasmmod finder treats each root as
a pack search path (same candidate order as VFS: `.zlib` preferred, then
`.aot{N}` / arch tags, then `.wasm`).

## Roots

```text
…/packs/                 # lead / latest channel ("current stable")
…/packs/@<version>/      # immutable version pin (e.g. @0.1.0)
```

Lead and pin directories use the **same filenames**. Promoting a release copies
(or uploads) artifacts into lead; pins stay forever.

## Artifact names

Aligned with wasmmod pack deliverables:

| File | Role |
|------|------|
| `hello.wasm` | Portable Wasm (interp / JIT hosts) |
| `hello.wasm.zlib` | MPZL wrap of the naked `.wasm` (preferred) |
| `hello.aot6` | AOT format-tagged (`N` = WAMR `AOT_CURRENT_VERSION`) |
| `hello.x86_64.aot6` | Arch-tagged AOT |
| `hello.aot6.zlib` / `hello.x86_64.aot6.zlib` | MPZL wraps |

Rules:

1. **Sign the naked artifact** (`.wasm` / `.aotN`) first — digest excludes `wasmmod.sig`.
2. Then wrap with MPZL (`.zlib`).
3. Do not put a trailing align pad after the AOT sig section (breaks WAMR load).

## Index

Each channel directory should eventually carry `index.json` (see
[INDEX.md](INDEX.md)). Lead’s index names the current stable version; pin
indexes are self-describing for that version only.

## Client resolution (today vs planned)

**Today (wasmmod):** `wasm.path` / `install_hook(url=…)` lists roots in priority
order; import/`load_pack` probes filenames under each root. No index fetch yet.

**Planned:** resolve `name` or `name@version` via `index.json`, then fetch the
listed artifact URLs (still multi-root fallback).
