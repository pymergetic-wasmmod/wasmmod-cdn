# Releases (setuptools-scm)

`pymergetic-metal-cdn` and `pymergetic-metal-cdn-client` share this git repo.
Versions come from **git tags** via [setuptools-scm](https://github.com/pypa/setuptools-scm).

## Tag format

Use annotated PEP 440 tags on a clean `main` commit:

```sh
# prerelease
./scripts/tag-release.sh 0.1.0a2

# or stable
./scripts/tag-release.sh 0.1.0
```

That creates `v0.1.0a2` (script prefixes `v` when missing).

## Verify

```sh
git describe --tags
pip install -e ./client -e ".[dev]" --config-settings editable_mode=compat
metal-cdn --version
# → 0.1.0a2  (no .devN+g… on the tagged commit)
```

## Push

```sh
git push origin main
git push origin v0.1.0a2
```

Untagged / dirty trees produce local development versions
(`0.1.0a2.dev1+gXXXX`), which is expected between releases.

## Client floor for wasmmod

After tagging **`v0.1.0a2`** (or later) and publishing the client wheel:

```sh
cd client && python -m build && twine upload dist/*
```

Update wasmmod [`requirements-publish.txt`](../../metalpython/extmod/wasmmod/requirements-publish.txt)
floor and `CLIENT_MIN_VERSION` only when wasmmod needs new client APIs
(already set to `>=0.1.0a2` for trust / files/raw / verify helpers).
