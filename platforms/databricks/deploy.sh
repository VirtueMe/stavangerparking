#!/usr/bin/env bash
# Deploy the bundle: dev with a wheel built from this checkout, prod with a released wheel.
#
#   platforms/databricks/deploy.sh dev
#   platforms/databricks/deploy.sh prod              # the latest release
#   platforms/databricks/deploy.sh prod v0.16.0      # a given release, e.g. to revert
#   platforms/databricks/deploy.sh --dry-run prod    # validate and show the plan, deploy nothing
#
# The jobs install whatever wheel is in dist/ (databricks.yml), so dist/ holds exactly one.
# A dry run still fills dist/, locally, because the plan needs the wheel; the workspace is untouched.
set -euo pipefail
cd "$(dirname "$0")"
dry_run=false
if [ "${1:-}" = --dry-run ]; then
  dry_run=true
  shift
fi
target=${1:?usage: deploy.sh [--dry-run] dev | deploy.sh [--dry-run] prod [release tag]}
repo=VirtueMe/stavangerparking

rm -rf dist
case "$target" in
  dev) uv build --wheel -o dist ../.. ;;
  prod)
    tag=${2:-$(gh release view --repo "$repo" --json tagName --jq .tagName)}
    echo "release $tag"
    gh release download "$tag" --repo "$repo" --pattern '*.whl' --dir dist
    ;;
  *) echo "unknown target: $target (dev or prod)" >&2; exit 2 ;;
esac
if $dry_run; then
  databricks bundle validate --target "$target"
  databricks bundle plan --target "$target"
else
  databricks bundle deploy --target "$target"
fi
