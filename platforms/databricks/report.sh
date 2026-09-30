#!/usr/bin/env bash
# Build the Power BI project against this platform's Unity Catalog tables, and publish it.
#
#   platforms/databricks/report.sh dev                # to "My workspace"
#   platforms/databricks/report.sh prod               # to "Stavanger Parking Case"
#   platforms/databricks/report.sh --dry-run prod     # build dist/powerbi-databricks-prod/ only
#
# The host comes from the CLI profile, the SQL warehouse from the workspace (DATABRICKS_WAREHOUSE
# names one if there are several), the catalog and schema from the bundle target, so none of them
# is in the repository. POWERBI_WORKSPACE overrides the Power BI workspace. Publishing needs
# `fab auth login` once (docs/report.md).
set -euo pipefail
cd "$(dirname "$0")"
dry_run=false
if [ "${1:-}" = --dry-run ]; then
  dry_run=true
  shift
fi
target=${1:?usage: report.sh [--dry-run] dev | prod}
case "$target" in
  dev) default_workspace="My workspace" ;;
  prod) default_workspace="Stavanger Parking Case" ;;
  *) echo "unknown target: $target (dev or prod)" >&2; exit 2 ;;
esac
workspace=${POWERBI_WORKSPACE:-$default_workspace}
if [ "$workspace" = "My workspace" ]; then
  fab_workspace="$workspace.Personal"
else
  fab_workspace="$workspace.Workspace"
fi

host=$(databricks auth describe --output json | jq -r '.details.host | sub("^https://"; "")')
warehouses=$(databricks warehouses list --output json)
if [ -n "${DATABRICKS_WAREHOUSE:-}" ]; then
  http_path=$(jq -r --arg name "$DATABRICKS_WAREHOUSE" \
    '.[] | select(.name == $name) | .odbc_params.path' <<<"$warehouses")
elif [ "$(jq length <<<"$warehouses")" -eq 1 ]; then
  http_path=$(jq -r '.[0].odbc_params.path' <<<"$warehouses")
else
  echo "set DATABRICKS_WAREHOUSE to one of: $(jq -r '[.[].name] | join(", ")' <<<"$warehouses")" >&2
  exit 2
fi
if [ -z "$http_path" ]; then
  echo "no SQL warehouse named '${DATABRICKS_WAREHOUSE:-}'" >&2
  exit 2
fi
# The schema's deployed name: development mode prefixes it with the deploying user
read -r catalog schema < <(databricks bundle validate --target "$target" --output json |
  jq -r '.resources.schemas.stavanger_parking | "\(.catalog_name) \(.name)"')

out=../../dist/powerbi-databricks-$target
uv run --no-project python ../../tools/powerbi.py build databricks "$out" \
  "host=$host" "http_path=$http_path" "catalog=$catalog" "schema=$schema"
if $dry_run; then
  echo "dry run: would publish to $workspace, reading $catalog.$schema through $http_path"
  exit 0
fi
uv run --only-group powerbi python ../../tools/powerbi.py publish "$out" "$fab_workspace"
