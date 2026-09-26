#!/usr/bin/env bash
set -euo pipefail

# Hermes CLI always uses HERMES_ROOT_PROFILE/profiles, while XNOBrain stores
# named agents in HERMES_PROFILES_ROOT. Point the CLI at that canonical root.
root="${HERMES_ROOT_PROFILE:-${HERMES_HOME:-}}"
profiles="${HERMES_PROFILES_ROOT:-}"
[[ -n "$root" && -n "$profiles" ]] || exit 0
native="$root/profiles"
[[ "$native" != "$profiles" ]] || exit 0

mkdir -p "$profiles"
if [[ -L "$native" ]]; then
  [[ "$(readlink -f "$native")" == "$(readlink -f "$profiles")" ]] || \
    echo "Hermes profiles link points outside the XNOBrain profiles root" >&2
  exit 0
fi
if [[ -d "$native" ]]; then
  # Existing nested profiles require an explicit, reviewed migration. Never
  # move or overwrite user profile data during container startup or a CLI run.
  if [[ -n "$(find "$native" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
    echo "Hermes has nested profiles at $native; migration required" >&2
    exit 0
  fi
  rmdir "$native"
elif [[ -e "$native" ]]; then
  echo "Hermes profiles path is occupied: $native" >&2
  exit 0
fi
ln -s "$profiles" "$native"
