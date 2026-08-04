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
git tag -d v0.1.0a3 && git push origin :refs/tags/v0.1.0a3
git tag -a v0.1.0a3 -m "metal-cdn 0.1.0a3"
git push origin v0.1.0a3
```

### Manual upload (always works)

```sh
gh run download <run-id> -n python-packages -D /tmp/metal-cdn-pypi
# or local build:
# python -m build --wheel --sdist --outdir dist-client client
# python -m build --wheel --sdist --outdir dist-server .
twine upload /tmp/metal-cdn-pypi/dist-client/* /tmp/metal-cdn-pypi/dist-server/*
```

Optional repo secret `PYPI_API_TOKEN` skips OIDC entirely.

## Verify

```sh
pip install -e ./client -e ".[dev]" --config-settings editable_mode=compat
metal-cdn --version   # → 0.1.0a3 on the tagged commit
```
