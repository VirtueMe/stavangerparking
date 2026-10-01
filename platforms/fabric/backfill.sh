#!/usr/bin/env bash
# Copy the raw files from the `data` branch into the Lakehouse's Files/, then run the pipeline once.
#
#   platforms/fabric/backfill.sh dev
#   platforms/fabric/backfill.sh --dry-run prod   # count the files and name the Lakehouse only
#
# Step 1 of the handover (ADR 011), and how Fabric gets data while GitHub Actions collects. Raw files
# never change once written, so copying them again is harmless; the pipeline loads only the files
# bronze does not have yet. The workspace comes from FABRIC_WORKSPACE (prod) or FABRIC_DEV_WORKSPACE
# (dev), as for deploy.sh. Not yet run against a real workspace (#16).
set -euo pipefail
cd "$(dirname "$0")"
# The Fabric CLI is in the dependency group "powerbi"; use it from there unless it is installed
command -v fab >/dev/null || fab() { uv run --quiet --only-group powerbi fab "$@"; }
dry_run=false
if [ "${1:-}" = --dry-run ]; then
  dry_run=true
  shift
fi
target=${1:?usage: backfill.sh [--dry-run] dev | prod}
case "$target" in
  dev) workspace=${FABRIC_DEV_WORKSPACE:?set FABRIC_DEV_WORKSPACE to the dev workspace} ;;
  prod) workspace=${FABRIC_WORKSPACE:?set FABRIC_WORKSPACE to the prod workspace} ;;
  *) echo "unknown target: $target (dev or prod)" >&2; exit 2 ;;
esac
lakehouse="$workspace.Workspace/StavangerParking.Lakehouse"

files=$(mktemp -d)
trap 'rm -rf "$files"' EXIT
git fetch --quiet origin data
git -C "$(git rev-parse --show-toplevel)" archive origin/data bronze | tar -x -C "$files"
count=$(find "$files" -type f | wc -l | tr -d ' ')
if $dry_run; then
  echo "dry run: would copy $count raw file(s) to $lakehouse/Files and run StavangerParkingPipeline ($target)"
  exit 0
fi
echo "copying $count raw file(s) to $lakehouse/Files"
# One file at a time, into the same relative path as on the data branch (raw_path in the config)
(cd "$files" && find bronze -type f) | while read -r path; do
  fab cp "$files/$path" "$lakehouse/Files/$path" -f >/dev/null
done

fab job run "$workspace.Workspace/StavangerParkingPipeline.DataPipeline"
