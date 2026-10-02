#!/usr/bin/env bash
# Run the pipeline job once with silver rebuilt from all of bronze, after a change to the parsing.
#
#   platforms/databricks/rebuild.sh dev
#   platforms/databricks/rebuild.sh --dry-run prod   # name the job and the parameter only
#
# Copies no raw files: it re-derives silver from the bronze table the platform already has, then
# gold and quality run as usual. Deploy the release with the new parsing first (tools/deploy).
set -euo pipefail
cd "$(dirname "$0")"
dry_run=false
if [ "${1:-}" = --dry-run ]; then
  dry_run=true
  shift
fi
target=${1:?usage: rebuild.sh [--dry-run] dev | prod}

if $dry_run; then
  echo "dry run: would run the pipeline job ($target) with rebuild_silver=true"
  exit 0
fi
databricks bundle run --target "$target" pipeline --params rebuild_silver=true
