# Browser MicroPython assets (not committed).

Populate from metalpython (wasmmod variant preferred for Try / `wasm.cdn`):

```bash
# after: make -C ports/webassembly VARIANT=wasmmod
./scripts/sync-repl-assets.sh /path/to/metalpython/ports/webassembly/build-wasmmod
```

Stock typing-only REPL (no pack import):

```bash
# after: make -C ports/webassembly VARIANT=standard
./scripts/sync-repl-assets.sh /path/to/metalpython/ports/webassembly/build-standard
```

Expected files:

- `micropython.mjs`
- `micropython.wasm`
