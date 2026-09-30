#!/usr/bin/env bash
# Run an operation on a platform: platforms/<platform>/<operation>.sh [--dry-run] <dev|prod> [args]
#
#   tools/platform.sh deploy -p databricks --prod
#
# Called by the tools named after the operations (tools/deploy, tools/backfill). The platform comes
# from -p/--platform, else PLATFORM in the environment, else PLATFORM in .env at the repository
# root. The target is dev unless --prod is given. --dry-run/-n is passed on to the platform script,
# which then shows what it would do and changes nothing. Other arguments go to the script as they
# are; an unknown option fails, and anything after -- is passed on unread.
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
operation=${1:?usage: platform.sh <operation> [options]}
shift

usage() {
  echo "usage: tools/$operation [-p|--platform <platform>] [--prod] [-n|--dry-run] [args...]" >&2
  exit 2
}

# The platforms that have a script for this operation, comma-separated
available() {
  local script names=()
  for script in "$root"/platforms/*/"$operation.sh"; do
    [ -f "$script" ] && names+=("$(basename "$(dirname "$script")")")
  done
  local IFS=,
  local list=${names[*]:-none}
  echo "${list//,/, }"
}

# PLATFORM from .env, read as a value rather than sourced: the file also holds secrets
dotenv_platform() {
  [ -f "$root/.env" ] || return 0
  sed -n 's/^[[:space:]]*\(export[[:space:]]\{1,\}\)\{0,1\}PLATFORM=//p' "$root/.env" |
    tail -n 1 | tr -d '\r' | sed "s/^[\"']//; s/[\"'][[:space:]]*$//; s/[[:space:]]*$//"
}

flag=""
target=dev
dry_run=false
args=()
while [ $# -gt 0 ]; do
  case "$1" in
    -p | --platform)
      [ $# -ge 2 ] || usage
      flag=$2
      shift 2
      ;;
    --platform=*)
      flag=${1#*=}
      shift
      ;;
    --prod)
      target=prod
      shift
      ;;
    -n | --dry-run)
      dry_run=true
      shift
      ;;
    -h | --help) usage ;;
    --)
      shift
      args+=("$@")
      break
      ;;
    # A mistyped option must stop here: passed on, a mistyped --dry-run would run for real
    -*)
      echo "unknown option: $1 (arguments for the script go after --)" >&2
      usage
      ;;
    *)
      args+=("$1")
      shift
      ;;
  esac
done

platform=${flag:-${PLATFORM:-$(dotenv_platform)}}
if [ -z "$platform" ]; then
  echo "no platform: pass -p <platform> or set PLATFORM in .env (platforms with $operation: $(available))" >&2
  exit 2
fi
script="$root/platforms/$platform/$operation.sh"
if [[ ! $platform =~ ^[A-Za-z0-9_-]+$ ]] || [ ! -f "$script" ]; then
  echo "platform '$platform' has no $operation (platforms with $operation: $(available))" >&2
  exit 2
fi

command=("$script")
if $dry_run; then command+=(--dry-run); fi
command+=("$target" ${args[@]+"${args[@]}"})
echo "$platform $target$($dry_run && echo ' (dry run)'): platforms/$platform/$operation.sh ${command[*]:1}" >&2
exec "${command[@]}"
