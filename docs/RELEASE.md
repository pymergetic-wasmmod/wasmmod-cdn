# Releases (setuptools-scm)

`pymergetic-metal-cdn` and `pymergetic-metal-cdn-client` share this git repo.
Versions come from **git tags** via [setuptools-scm](https://github.com/pypa/setuptools-scm).

## Tag format

```sh
./scripts/tag-release.sh 0.1.0a3   # → annotated tag v0.1.0a3
git push origin main
git push origin v0.1.0a3
```

## PyPI Trusted Publishing (monorepo)

PyPI allows **only one pending publisher per (repo + workflow file)**.
This repo therefore has **two** workflows:

| Workflow file | PyPI project |
|---------------|--------------|
| `publish-pypi-client.yml` | `pymergetic-metal-cdn-client` |
| `publish-pypi-server.yml` | `pymergetic-metal-cdn` |

### Pending publishers (new projects)

Account → Publishing → pending publisher — **twice**:

**Client**

| Field | Value |
|-------|--------|
| PyPI Project Name | `pymergetic-metal-cdn-client` |
| Owner | `pymergetic` |
| Repository | `metal-cdn` |
| Workflow name | `publish-pypi-client.yml` |
| Environment | *(empty)* |

**Server**

| Field | Value |
|-------|--------|
| PyPI Project Name | `pymergetic-metal-cdn` |
| Owner | `pymergetic` |
| Repository | `metal-cdn` |
| Workflow name | `publish-pypi-server.yml` |
| Environment | *(empty)* |

Remove any old pending row that still points at `publish-pypi.yml` (that
filename is gone / cannot cover both packages).

### Ship / re-tag

After the two pending publishers exist and the split workflows are on `main`:

```sh
# Prefer a new tag (setuptools-scm); example next alpha:
./scripts/tag-release.sh 0.1.0a7
git push origin main
git push origin v0.1.0a7
```

Both `publish-pypi-client` and `publish-pypi-server` run on the same `v*` tag.
First **OIDC** upload graduates each pending publisher into a normal one on the
new PyPI project. Do **not** set `password:` / `PYPI_API_TOKEN` on those jobs —
token upload skips Trusted Publishing and leaves the pending row stuck.

### Manual upload (escape hatch)

```sh
gh run download <run-id> -n python-packages -D /tmp/metal-cdn-pypi
# or local build:
# python -m build --wheel --sdist --outdir dist-client client
# python -m build --wheel --sdist --outdir dist-server .
twine upload /tmp/metal-cdn-pypi/dist-client/* /tmp/metal-cdn-pypi/dist-server/*
```

Manual `twine` + API token creates/uploads the project but does **not** convert
a pending Trusted Publisher; add a normal publisher on the project afterward if
you used that path.

## Verify

```sh
pip install -e ./client -e ".[dev]" --config-settings editable_mode=compat
metal-cdn --version   # → 0.1.0a3 on the tagged commit
```
