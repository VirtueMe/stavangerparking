#!/usr/bin/env bash
# Deploy the bundle: dev with a wheel built from this checkout, prod with a released wheel.
#
#   platforms/databricks/deploy.sh dev
#   platforms/databricks/deploy.sh prod v0.17.0
#
# The jobs install whatever wheel is in dist/ (databricks.yml), so dist/ holds exactly one.
set -euo pipefail
cd "$(dirname "$0")"
target=${1:?usage: deploy.sh dev | deploy.sh prod <release tag>}

rm -rf dist
case "$target" in
  dev) uv build --wheel -o dist ../.. ;;
  prod)
    tag=${2:?prod installs a released wheel: deploy.sh prod <release tag>}
    gh release download "$tag" --repo VirtueMe/stavangerparking --pattern '*.whl' --dir dist
    ;;
  *) echo "unknown target: $target (dev or prod)" >&2; exit 2 ;;
esac
databricks bundle deploy --target "$target"
