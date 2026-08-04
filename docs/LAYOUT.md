# Channel layout

Static object tree served over HTTP(S). The wasmmod finder treats each root as
a pack search path (same candidate order as VFS: `.zlib` preferred, then
`.elf` / arch tags when enabled, then `.aot{N}` / arch tags, then `.wasm`).

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
| `hello.elf` | In-tree ELF64 ET_REL (host arch) |
| `hello.x86_64.elf` / `hello.aarch64.elf` | Arch-tagged ELF twins |
| `hello.elf.zlib` / `hello.x86_64.elf.zlib` | MPZL wraps of ELF |
| `hello.aot6` | AOT format-tagged (`N` = WAMR `AOT_CURRENT_VERSION`) |
| `hello.x86_64.aot6` | Arch-tagged AOT |
| `hello.aot6.zlib` / `hello.x86_64.aot6.zlib` | MPZL wraps |

Rules:

1. **Sign the naked artifact** (`.wasm` / `.elf` / `.aotN`) first — digest excludes `wasmmod.sig`.
2. Then wrap with MPZL (`.zlib`). CDN `publish` also auto-adds missing `.zlib` twins.
3. Do not put a trailing align pad after the AOT sig section (breaks WAMR load).
4. **One publish per package version** must include every twin (Wasm + ELF + arch).
   Package entries replace `artifacts` wholesale — splitting `hello.wasm` and
   `hello.elf` across two publishes drops the first set. `scripts/dev-up.sh`
   seeds multi-file for that reason.

## Index

Each channel directory should eventually carry `index.json` (see
[INDEX.md](INDEX.md)). Lead’s index names the current stable version; pin
indexes are self-describing for that version only.

## Client resolution (today vs planned)

**Today (wasmmod):** `wasm.path` / `install_hook(url=…)` lists roots in priority
order; import/`load_pack` probes filenames under each root. No index fetch yet.

**Planned:** resolve `name` or `name@version` via `index.json`, then fetch the
listed artifact URLs (still multi-root fallback).

## Inspect HTTP (metal-cdn API)

Thin wrap over shared `wasmmod_inspect` (not a private parser). Prefix:
`/cdn/artifacts/lead/<file>/…` or `/cdn/artifacts/pin/<ver>/<file>/…`.

| GET | Role |
|-----|------|
| `…/inspect` | Aggregate (pack/source/sig/sections/**symbols**/`has_dwarf`) |
| `…/symbols` | Symbol list |
| `…/addr2line?addr=` | Location[] for address |
| `…/locations?name=` | Location[] for symbol (multi-loc OK) |
| `…/disasm?index=&offset=&limit=` | Disasm lines (ELF **shndx** / Wasm code window) |
| `…/sections` · `…/sections/raw?index=&offset=&limit=` | Section list / ranged bytes |
| `…/files` · `…/files/raw` · `…/files/mpy-disasm?path=` | Embedded files + basic mpy-dis |

UI: package page **Open Inspect…** / symbol & export clicks →
`window.openInspect({ filename, symbol?, sectionIndex?, mpyPath?, … })`
(`static/inspect.js`). ELF section indexes are real `shndx` (match symbols).
