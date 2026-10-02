#!/usr/bin/env bash
# Record the Collect workflow's failed runs that left no record of their own (#119)
#
#   .github/scripts/record-failed-runs.sh <storage>
#
# A run whose push failed, or that crashed, cannot record that itself: the next run does, here.
# Asks the GitHub API for the workflow's failed runs (one call), lets the collector say which have
# no record yet, asks for the failed step of those only, and has the collector write the records
# into <storage>, the data branch's worktree. Needs GH_TOKEN with `actions: read` and
# GITHUB_REPOSITORY. The package only sees a list of runs, so it stays platform-neutral.
set -euo pipefail
storage=${1:?usage: record-failed-runs.sh <storage>}
repo=${GITHUB_REPOSITORY:?set GITHUB_REPOSITORY to owner/name}
collect=(uv run --no-dev python -m stavanger_parking.bronze.collect)

# The latest attempt of each failed run; run ids as the collector writes them, <id>-<attempt>
runs=$(gh api "repos/$repo/actions/workflows/collect.yml/runs?status=failure&per_page=100" \
  --jq '[.workflow_runs[] | {run_id: "\(.id)-\(.run_attempt)", started_at: .run_started_at, url: .html_url}]')
args=()
for run_id in $(jq -r '.[].run_id' <<<"$runs"); do
  args+=(--run-id "$run_id")
done
new=$("${collect[@]}" unrecorded-runs --storage "$storage" ${args[@]+"${args[@]}"})
if [ -z "$new" ]; then
  echo "no failed runs to record"
  exit 0
fi

file=$(mktemp)
trap 'rm -f "$file"' EXIT
echo '[]' >"$file"
for run_id in $new; do
  step=$(gh api "repos/$repo/actions/runs/${run_id%-*}/attempts/${run_id##*-}/jobs" \
    --jq '[.jobs[].steps[] | select(.conclusion == "failure") | .name][0] // ""')
  jq --arg id "$run_id" --arg step "$step" --argjson runs "$runs" \
    '. + [$runs[] | select(.run_id == $id) | . + {failed_step: (if $step == "" then null else $step end)}]' \
    "$file" >"$file.next"
  mv "$file.next" "$file"
done
"${collect[@]}" record-failed-runs --storage "$storage" --runs "$file"
