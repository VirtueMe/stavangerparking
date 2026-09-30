#!/usr/bin/env bash
# Copy the raw files from the `data` branch into the raw volume, then run the pipeline once.
#
#   platforms/databricks/backfill.sh dev
#   platforms/databricks/backfill.sh --dry-run prod   # count the files and name the volume only
#
# Step 1 of the handover (ADR 011), and how the platform gets data while GitHub Actions collects.
# Raw files never change once written, so copying them again is harmless; the pipeline loads
# only the files bronze does not have yet.
set -euo pipefail
cd "$(dirname "$0")"
dry_run=false
if [ "${1:-}" = --dry-run ]; then
  dry_run=true
  shift
fi
target=${1:?usage: backfill.sh [--dry-run] dev | prod}

# The schema's deployed name: development mode prefixes it with the deploying user
schema=$(databricks bundle validate --target "$target" --output json |
  jq -r '.resources.schemas.stavanger_parking | "\(.catalog_name)/\(.name)"')
raw="/Volumes/$schema/raw"

files=$(mktemp -d)
trap 'rm -rf "$files"' EXIT
git fetch --quiet origin data
git -C "$(git rev-parse --show-toplevel)" archive origin/data bronze | tar -x -C "$files"
count=$(find "$files" -type f | wc -l | tr -d ' ')
if $dry_run; then
  echo "dry run: would copy $count raw file(s) to $raw and run the pipeline job ($target)"
  exit 0
fi
echo "copying $count raw file(s) to $raw"
databricks fs cp --recursive --overwrite "$files/bronze" "dbfs:$raw/bronze"

databricks bundle run --target "$target" pipeline
