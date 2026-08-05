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

### Existing projects (normal publisher)

If the PyPI project **already exists** (e.g. earlier `twine` / API-token upload),
do **not** use a pending publisher. Pending + existing name fails with:

`invalid-pending-publisher: valid token, but project already exists`

That failed exchange often **deletes** the pending row. Fix on each project:

1. https://pypi.org/manage/project/pymergetic-metal-cdn/settings/publishing/
2. https://pypi.org/manage/project/pymergetic-metal-cdn-client/settings/publishing/

Add a **Trusted Publisher** (not pending) with:

| Field | Client | Server |
|-------|--------|--------|
| Owner | `pymergetic` | `pymergetic` |
| Repository | `metal-cdn` | `metal-cdn` |
| Workflow name | `publish-pypi-client.yml` | `publish-pypi-server.yml` |
| Environment | *(empty)* | *(empty)* |

**Filename including `.yml`** — not the workflow `name:` title, not
`publish-pypi.yml`. a6 shipped with an API token; OIDC only works after these
rows exist on the **project** publishing pages.

Then re-run the failed workflow(s) (or tag again). No new tag needed if the
tag’s workflow already has the fix and publishers match.

Workflows prefer OIDC. Optional escape hatch: repo secret **`PYPI_API_TOKEN`**
(scope covering both packages) — same path as a6. Remove that secret once
project Trusted Publishers work.

If upload fails with `OIDC scoped token is not valid for project …`, the
publisher row above is missing or the workflow filename is mistyped.

### Pending publishers (new projects only)

Account → Publishing → pending publisher — only when the name is **not** on PyPI yet:

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

```sh
# Prefer a new tag (setuptools-scm); next after a9:
./scripts/tag-release.sh 0.1.0a10
git push origin main
git push origin v0.1.0a10
```

Both `publish-pypi-client` and `publish-pypi-server` run on the same `v*` tag.
First OIDC upload on a **pending** publisher creates the project and graduates
to a normal publisher. On an **existing** project, use a normal publisher (above).

### Manual / secret escape hatch

Repo secret `PYPI_API_TOKEN` → workflows upload with that token instead of OIDC.

Or build locally / download artifacts and:

```sh
twine upload dist-client/* dist-server/*
```

Manual `twine` does **not** create a Trusted Publisher; add the project
publisher rows afterward if you want OIDC next time.

## Verify

```sh
pip install -e ./client -e ".[dev]" --config-settings editable_mode=compat
metal-cdn --version   # → 0.1.0a3 on the tagged commit
```
