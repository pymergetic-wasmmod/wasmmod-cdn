# Browser MicroPython assets (not committed)

Three **sibling** checkouts next to `metal-cdn` (or set env). Pill order: **mp · mpwm · upy**.

| Dir | Env | Sibling folder | Role |
|-----|-----|----------------|------|
| `mp/` | `METALPYTHON` | `../metalpython` | build of metal **arch.wasm** (also published to CDN) |
| `mpwm/` | `METALPYTHON_WM` | `../metalpython-wasmmod` | upy + wasmmod only |
| `upy/` | `MICROPYTHON` | `../micropython` | vanilla upstream µPy |

**`mp` source of truth:** lead pack `pymergetic.metal.arch.wasm` (`.mjs` + `.wasm`).
`static/repl/mp/` is a local fallback when that pack is missing (dev before `--firmware`).

`mp` autoexec is metal’s `arch/wasm/autoexec.py` (post-ready CDN hook only; boot tree runs in C).
`mpwm`/`upy` keep the CDN shell template. Arch / host / kernel packs stay Inspect-only for guest Play.

Spines (metal): **arch** seat · **port** µPy entry · **wamr_host** (firmware hosts guests) · **wasmmod** engine.

metal-cdn does **not** assume an os-sdk `packages/` layout — only env or `../name`.

```bash
./scripts/dev-up.sh
./scripts/dev-up.sh --firmware-only   # publish arch.x86_64 + arch.wasm
```
