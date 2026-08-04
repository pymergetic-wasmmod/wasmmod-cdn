# Releases (setuptools-scm)

`pymergetic-metal-cdn` and `pymergetic-metal-cdn-client` share this git repo.
Versions come from **git tags** via [setuptools-scm](https://github.com/pypa/setuptools-scm).

## Tag format

Use annotated PEP 440 tags on a clean `main` commit:

```sh
# prerelease
./scripts/tag-release.sh 0.1.0a3

# or stable
./scripts/tag-release.sh 0.1.0
```

That creates `v0.1.0a3` (script prefixes `v` when missing).

## Verify

```sh
git describe --tags
pip install -e ./client -e ".[dev]" --config-settings editable_mode=compat
metal-cdn --version
# → 0.1.0a3  (no .devN+g… on the tagged commit)
```

## Push (triggers CI + PyPI)

```sh
git push origin main
git push origin v0.1.0a3
```

Pushing a `v*` tag runs [`.github/workflows/publish-pypi.yml`](../.github/workflows/publish-pypi.yml):
builds both wheels and publishes them via **Trusted Publishing** (OIDC).

Configure once on PyPI for each project (`pymergetic-metal-cdn-client`,
`pymergetic-metal-cdn`):

- Owner: `pymergetic`
- Repository: `metal-cdn`
- Workflow: `publish-pypi.yml`
- Environment: `pypi`

Dry-run (build only): Actions → **publish-pypi** → Run workflow → dry_run=true.

Untagged / dirty trees produce local development versions
(`0.1.0a3.dev1+gXXXX`), which is expected between releases.

## Manual upload (fallback)

```sh
python -m build --outdir dist-client client
python -m build --outdir dist-server .
twine upload dist-client/* dist-server/*
```

## Client floor for wasmmod

After tagging and publishing the client wheel, bump wasmmod
[`requirements-publish.txt`](../../metalpython/extmod/wasmmod/requirements-publish.txt)
only when wasmmod needs newer client APIs.
