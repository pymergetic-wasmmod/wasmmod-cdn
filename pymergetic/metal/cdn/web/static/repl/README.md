# Browser MicroPython assets (not committed)

Three **sibling** checkouts next to `metal-cdn` (or set env). Pill order: **mp · mpwm · upy**.

| Dir | Env | Sibling folder | Role |
|-----|-----|----------------|------|
| `mp/` | `METALPYTHON` | `../metalpython` | mpwm + metalmod |
| `mpwm/` | `METALPYTHON_WM` | `../metalpython-wasmmod` | upy + wasmmod |
| `upy/` | `MICROPYTHON` | `../micropython` | vanilla upstream µPy |

metal-cdn does **not** assume an os-sdk `packages/` layout — only env or `../name`.

```bash
./scripts/dev-up.sh
```
