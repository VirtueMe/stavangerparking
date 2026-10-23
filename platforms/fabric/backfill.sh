#!/usr/bin/env bash
# Copy the raw files from the `data` branch into the Lakehouse's Files/, then run the pipeline once.
#
#   platforms/fabric/backfill.sh dev
#   platforms/fabric/backfill.sh --dry-run prod   # count the files and name the Lakehouse only
#
# Step 1 of the handover (ADR 011), and how Fabric gets data while GitHub Actions collects. Raw files
# never change once written, so only the files the Lakehouse lacks are copied (a run that stopped
# halfway is finished by the next one), and the run ends by listing the Lakehouse again: it fails
# if a file is still missing. The pipeline loads only the files bronze does not have yet. The
# workspace comes from FABRIC_WORKSPACE (prod) or FABRIC_DEV_WORKSPACE (dev), as for deploy.sh.
set -euo pipefail
cd "$(dirname "$0")"
# The Fabric CLI is in the dependency group "powerbi"; use it from there unless it is installed.
# It becomes a script on the PATH, not a function, as `timeout` and xargs start programs (--project,
# as the data folder is not in the project; < /dev/null, as fab would read the stdin of xargs)
if ! command -v fab >/dev/null; then
  bin=$(mktemp -d)
  root=$(cd ../.. && pwd)
  printf '#!/usr/bin/env bash\nexec uv run --quiet --project "%s" --only-group powerbi fab "$@" </dev/null\n' "$root" >"$bin/fab"
  chmod +x "$bin/fab"
  PATH="$bin:$PATH"
fi
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
git fetch --quiet origin data
git -C "$(git rev-parse --show-toplevel)" archive origin/data bronze | tar -x -C "$files"
count=$(find "$files" -type f | wc -l | tr -d ' ')
all=$(mktemp)
(cd "$files" && find bronze -type f | sort) >"$all"

# The files the Lakehouse lacks. One listing per folder, as `fab ls` is not recursive: a folder that
# is not there lists as nothing, so all its files are missing. Each listing goes to a file of its
# own, because parallel writes to one file interleave their lines. The CLI ends its lines with a
# carriage return, which is taken off (#129)
listings=$(mktemp -d)
missing=$(mktemp)
trap 'rm -rf "$files" "$all" "$listings" "$missing" "${bin:-}"' EXIT
find_missing() {
  rm -f "$listings"/*
  (cd "$files" && find bronze -type d) |
    xargs -P 8 -I{} bash -c 'fab ls "$1/Files/{}" 2>/dev/null | tr -d "\r" | sed "s|^|{}/|" >"$2/$(echo {} | tr / _)"' _ "$lakehouse" "$listings"
  cat "$listings"/* | sort >"$listings.have"
  comm -23 "$all" "$listings.have" >"$missing"
  rm -f "$listings.have"
  wc -l <"$missing" | tr -d ' '
}
todo=$(find_missing)

if $dry_run; then
  echo "dry run: would copy $todo of $count raw file(s) to $lakehouse/Files and run StavangerParkingPipeline ($target)"
  exit 0
fi

# fab cp does not make folders, and cannot copy a folder from a local path: make them, then copy the
# files one call each, eight at a time and each with a time limit (a lapsed sign-in hangs the CLI)
copy_missing() {
  sed 's|/[^/]*$||' "$missing" | sort -u | while read -r dir; do
    fab mkdir "$lakehouse/Files/$dir" >/dev/null 2>&1 || true
  done
  xargs -P 8 -I{} bash -c 'timeout 120 fab cp "$1/{}" "$2/Files/{}" -f >/dev/null 2>&1 || echo "failed: {}" >&2' _ "$files" "$lakehouse" <"$missing"
}
echo "copying $todo of $count raw file(s) to $lakehouse/Files"
if [ "$todo" -gt 0 ]; then
  copy_missing
  left=$(find_missing)
  if [ "$left" -gt 0 ]; then
    echo "$left file(s) did not arrive; trying them once more"
    copy_missing
    left=$(find_missing)
  fi
  if [ "$left" -gt 0 ]; then
    echo "$left of $count raw file(s) are still missing from $lakehouse/Files, for example:" >&2
    head -3 "$missing" >&2
    exit 1
  fi
fi
echo "all $count raw file(s) are in $lakehouse/Files"

fab job run "$workspace.Workspace/StavangerParkingPipeline.DataPipeline"
