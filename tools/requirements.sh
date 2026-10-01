#!/usr/bin/env bash
# Write the runtime dependencies at the versions in uv.lock, as a requirements file (ADR 012, #101).
#
#   tools/requirements.sh <out file>                 # this checkout's uv.lock
#   tools/requirements.sh <out file> v0.21.0         # that release's own uv.lock, so a revert gets its versions
#   tools/requirements.sh <out file> v0.21.0 --no-hashes
#
# Every version is pinned and, unless --no-hashes, every file's hash is listed, so pip installs
# exactly what CI tested; the dev, release and Power BI groups are left out. Used by each platform's
# deploy.sh: uv.lock is the one place that says which versions run, everywhere.
set -euo pipefail
out=${1:?usage: requirements.sh <out file> [release tag] [--no-hashes]}
tag=""
hashes=()
for arg in "${@:2}"; do
  case "$arg" in
    --no-hashes) hashes=(--no-hashes) ;;
    *) tag=$arg ;;
  esac
done

root=$(cd "$(dirname "$0")/.." && pwd)
project=$root
if [ -n "$tag" ]; then
  project=$(mktemp -d)
  trap 'rm -rf "$project"' EXIT
  git -C "$root" fetch --quiet origin tag "$tag"
  git -C "$root" archive "$tag" pyproject.toml uv.lock | tar -x -C "$project"
fi
uv export --frozen --no-dev --no-emit-project --no-header --format requirements-txt --quiet \
  ${hashes[@]+"${hashes[@]}"} --project "$project" -o "$out"
