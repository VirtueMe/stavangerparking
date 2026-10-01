#!/usr/bin/env bash
# Build the Power BI project against the Lakehouse's tables, and publish it to the same workspace.
#
#   platforms/fabric/report.sh dev
#   platforms/fabric/report.sh --dry-run prod   # build dist/powerbi-fabric-prod/ only
#
# The model reads the Lakehouse through its SQL analytics endpoint, in import mode
# (report/expressions.tmdl). The workspace comes from FABRIC_WORKSPACE (prod) or FABRIC_DEV_WORKSPACE
# (dev); POWERBI_WORKSPACE publishes somewhere else. Not yet run against a real workspace (#16).
set -euo pipefail
cd "$(dirname "$0")"
# The Fabric CLI is in the dependency group "powerbi"; use it from there unless it is installed
command -v fab >/dev/null || fab() { uv run --quiet --only-group powerbi fab "$@"; }
dry_run=false
if [ "${1:-}" = --dry-run ]; then
  dry_run=true
  shift
fi
target=${1:?usage: report.sh [--dry-run] dev | prod}
case "$target" in
  dev) workspace=${FABRIC_DEV_WORKSPACE:?set FABRIC_DEV_WORKSPACE to the dev workspace} ;;
  prod) workspace=${FABRIC_WORKSPACE:?set FABRIC_WORKSPACE to the prod workspace} ;;
  *) echo "unknown target: $target (dev or prod)" >&2; exit 2 ;;
esac
publish_to=${POWERBI_WORKSPACE:-$workspace}

sql_endpoint=$(fab get "$workspace.Workspace/StavangerParking.Lakehouse" \
  -q properties.sqlEndpointProperties.connectionString)
if [ -z "$sql_endpoint" ]; then
  echo "the Lakehouse in $workspace has no SQL analytics endpoint yet; deploy it first" >&2
  exit 2
fi

out=../../dist/powerbi-fabric-$target
uv run --no-project python ../../tools/powerbi.py build fabric "$out" \
  "sql_endpoint=$sql_endpoint" "lakehouse=StavangerParking"
if $dry_run; then
  echo "dry run: would publish to $publish_to, reading StavangerParking through $sql_endpoint"
  exit 0
fi
uv run --only-group powerbi python ../../tools/powerbi.py publish "$out" "$publish_to.Workspace"
