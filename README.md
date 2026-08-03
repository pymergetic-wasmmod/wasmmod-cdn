# metal-cdn

**v0.1.0a1 (scaffold)** — CDN / channel publish software for
[wasmmod](https://github.com/pymergetic/wasmmod) packs.

> **Experimental.** This repo is a stub: layout + index schema + a `publish`
> CLI skeleton. It does not yet write trees, fetch indexes, or resolve deps.
> The load/trust/compress path lives in wasmmod; this repo owns distribution.

## Role

| Repo | Owns |
|------|------|
| [wasmmod](https://github.com/pymergetic/wasmmod) | Pack format, sign, MPZL zlib, AOT `.aot{N}`, finder, `wasm.install_hook` |
| **metal-cdn** | Channel layout, `index.json`, publish into lead + `@version` pins |
| [metalpython](https://github.com/pymergetic/metalpython) | Host that submodules wasmmod |

Clients already support multi-root priority:

```python
import wasm
wasm.install_hook(url=[
    "https://cdn.example/packs/",      # primary
    "https://mirror.example/packs/",   # fallback
])
```

## Channel layout (target)

```text
packs/                      # lead / latest
packs/@0.1.0/               # version pin
  hello.wasm.zlib
  hello.x86_64.aot6.zlib
  index.json
```

Details: [docs/LAYOUT.md](docs/LAYOUT.md) · index schema: [docs/INDEX.md](docs/INDEX.md) ·
roadmap: [docs/ROADMAP.md](docs/ROADMAP.md).

Pack artifact rules (sign before zlib, AOT format tag): wasmmod
[docs/PACK.md](https://github.com/pymergetic/wasmmod/blob/main/docs/PACK.md).

## CLI (scaffold)

```sh
python3 -m metal_cdn.publish publish --help
# or after install: metal-cdn publish --help
```

`publish` exits `2` until implemented — it only documents the intended inputs
from wasmmod `pack` / `sign` / `zlib`.

## Status

Scaffold only. Next: real `publish`, then client pin + index fetch in wasmmod.

## License

MIT — Rouven Raudzus (`raudzus@pymergetic.com`) — [pymergetic](https://github.com/pymergetic)
