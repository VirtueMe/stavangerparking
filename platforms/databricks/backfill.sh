#!/usr/bin/env bash
# Copy the raw files from the `data` branch into the raw volume, then run the pipeline once.
#
#   platforms/databricks/backfill.sh dev
#   platforms/databricks/backfill.sh --dry-run prod   # count the files and name the volume only
#
# Step 1 of the handover (ADR 011), and how the platform gets data while GitHub Actions collects.
# Raw files never change once written, so only the files the volume lacks are copied (a run that
# stopped halfway is finished by the next one); the pipeline loads only the files bronze does not
# have yet.
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
trap 'rm -rf "$files" "${listings:-}" "${have:-}" "${missing:-}"' EXIT
git fetch --quiet origin data
git -C "$(git rev-parse --show-toplevel)" archive origin/data bronze | tar -x -C "$files"
count=$(find "$files" -type f | wc -l | tr -d ' ')

# What the volume has: one listing per folder, as `fs ls` is not recursive. A folder the volume
# lacks lists as nothing, so all its files are missing. Each listing goes to a file of its own,
# because parallel writes to one file interleave their lines
listings=$(mktemp -d)
(cd "$files" && find bronze -type d) |
  xargs -P 8 -I{} sh -c 'databricks fs ls "dbfs:$1/{}" 2>/dev/null </dev/null | sed "s|^|{}/|" >"$2/$(echo {} | tr / _)"' _ "$raw" "$listings"
have=$(mktemp)
cat "$listings"/* >"$have"
missing=$(mktemp)
(cd "$files" && find bronze -type f | sort) | comm -23 - <(sort "$have") >"$missing"
todo=$(wc -l <"$missing" | tr -d ' ')

if $dry_run; then
  echo "dry run: would copy $todo of $count raw file(s) to $raw and run the pipeline job ($target)"
  exit 0
fi
echo "copying $todo of $count raw file(s) to $raw"
if [ "$todo" -gt 0 ]; then
  xargs -P 8 -I{} databricks fs cp --overwrite "$files/{}" "dbfs:$raw/{}" <"$missing" >/dev/null
fi

databricks bundle run --target "$target" pipeline
