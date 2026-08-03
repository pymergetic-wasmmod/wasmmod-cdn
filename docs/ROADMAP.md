# Roadmap

Ordered for a useful CDN without boiling the ocean.

## 1. Index + layout (this scaffold)

- [x] Document `packs/` + `packs/@version/` layout
- [x] Propose `index.json` schema (exact deps)
- [x] CLI stub (`metal-cdn publish --help`)
- [ ] Implement `publish`: copy signed (+ zlib) artifacts, write index

## 2. Pins ≠ lead (wasmmod client)

- [ ] Resolve `name@version` / `@version` roots without rewriting lead
- [ ] `install_hook` / finder: optional pin preference API
- [ ] Fetch `index.json` when present; fall back to today’s filename probe

## 3. Exact deps

- [ ] Pack / index declares `deps: { peer: "0.1.0" }`
- [ ] Loader pulls missing deps in pin order (multi-URL priority unchanged)
- [ ] No ranges until exact path is boring and correct

## 4. Publish pipeline

- [ ] One command: pack → AOT → sign → zlib → lead + `@v` + index
- [ ] CI: promote tag → pin; optional lead update
- [ ] Smoke against `wasmmod.py httpd` + `test-http` / signed verify

## 5. Later

- Yank / redirect
- Multi-arch matrix in index
- Cache / ETag / conditional GET on the C fetch client
- Signed index (optional; packs already signed)
