#!/usr/bin/env bash
# Create an annotated setuptools-scm tag for metal-cdn (+ client).
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 <pep440-version>   e.g. 0.1.0a2 or 0.1.0" >&2
  exit 2
fi

ver="$1"
tag="$ver"
if [[ "$tag" != v* ]]; then
  tag="v${tag}"
fi

root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "not a git repository" >&2
  exit 1
fi

if [[ -n "$(git status --porcelain)" ]]; then
  echo "working tree is dirty — commit or stash before tagging" >&2
  git status -sb >&2
  exit 1
fi

if git rev-parse "$tag" >/dev/null 2>&1; then
  echo "tag already exists: $tag" >&2
  exit 1
fi

git tag -a "$tag" -m "metal-cdn ${ver#v}"
echo "created $tag → $(git rev-parse --short HEAD)"
echo "verify: pip install -e . && metal-cdn --version"
echo "push:   git push origin $tag"
