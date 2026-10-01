#!/usr/bin/env bash
# Deploy the Fabric items: dev with a wheel built from this checkout, prod with a released wheel.
#
#   platforms/fabric/deploy.sh dev
#   platforms/fabric/deploy.sh prod                # the latest release
#   platforms/fabric/deploy.sh prod v0.16.0        # a given release, e.g. to revert
#   platforms/fabric/deploy.sh --dry-run prod      # prepare dist/ and check the workspace, deploy nothing
#
# The items in workspace/ are deployed with fabric-cicd (`fab deploy`), which creates or updates them
# and fills in the IDs parameter.yml names. The wheel's dependencies are installed at the versions in
# uv.lock (the checkout's for dev, the release tag's for prod; tools/requirements.sh, ADR 012), as CI
# tests them. The workspace is not in the repository: FABRIC_WORKSPACE names the prod workspace,
# FABRIC_DEV_WORKSPACE the dev one. FABRIC_ALERT_EMAIL is where a failed pipeline e-mails (default:
# the signed-in account). Not yet run against a real workspace (#16).
set -euo pipefail
cd "$(dirname "$0")"
# The Fabric CLI is in the dependency group "powerbi"; use it from there unless it is installed
command -v fab >/dev/null || fab() { uv run --quiet --only-group powerbi fab "$@"; }
dry_run=false
if [ "${1:-}" = --dry-run ]; then
  dry_run=true
  shift
fi
target=${1:?usage: deploy.sh [--dry-run] dev | deploy.sh [--dry-run] prod [release tag]}
repo=VirtueMe/stavangerparking
case "$target" in
  dev) workspace=${FABRIC_DEV_WORKSPACE:?set FABRIC_DEV_WORKSPACE to the dev workspace} ;;
  prod) workspace=${FABRIC_WORKSPACE:?set FABRIC_WORKSPACE to the prod workspace} ;;
  *) echo "unknown target: $target (dev or prod)" >&2; exit 2 ;;
esac
alert=${FABRIC_ALERT_EMAIL:-$(fab auth status 2>/dev/null | sed -n 's/^Account: *//p')}

out=$(cd ../.. && pwd)/dist/fabric-$target
rm -rf "$out"
mkdir -p "$out/wheel"
case "$target" in
  dev)
    uv build --wheel -o "$out/wheel" ../..
    ../../tools/requirements.sh "$out/wheel/requirements.txt"
    ;;
  prod)
    tag=${2:-$(gh release view --repo "$repo" --json tagName --jq .tagName)}
    echo "release $tag"
    gh release download "$tag" --repo "$repo" --pattern '*.whl' --dir "$out/wheel"
    # The lock file of that release, not of this checkout
    ../../tools/requirements.sh "$out/wheel/requirements.txt" "$tag"
    ;;
esac
wheel_file=$(cd "$out/wheel" && ls -- *.whl)

# The items with this deployment's values; the repository keeps the placeholders
cp -r workspace "$out/workspace"
find "$out/workspace" -name notebook-content.py -exec sed -i "s|{{wheel}}|$wheel_file|g" {} +
sed -i "s|{{alert_email}}|$alert|g" "$out/workspace/StavangerParkingPipeline.DataPipeline/pipeline-content.json"
# A placeholder nobody filled in would reach Fabric as text
if grep -rn '{{' "$out/workspace"; then
  echo "unfilled placeholders in $out/workspace" >&2
  exit 2
fi
cat >"$out/workspace/config.yml" <<EOF
core:
  workspace:
    $target: "$workspace"
  repository_directory: "."
  item_types_in_scope:
    - Lakehouse
    - Notebook
    - DataPipeline
  parameter: "parameter.yml"
EOF

if $dry_run; then
  fab exists "$workspace.Workspace"
  echo "dry run: would deploy $(find "$out/workspace" -name .platform | wc -l | tr -d ' ') item(s) to $workspace, with $wheel_file"
  exit 0
fi
fab deploy --config "$out/workspace/config.yml" --target_env "$target" -f
for file in requirements.txt "$wheel_file"; do
  fab cp "$out/wheel/$file" "$workspace.Workspace/StavangerParking.Lakehouse/Files/wheels/$file" -f
done
