#!/usr/bin/env bash
set -euo pipefail

# Hermes CLI always uses HERMES_ROOT_PROFILE/profiles, while XNOBrain stores
# named agents in HERMES_PROFILES_ROOT. Point the CLI at that canonical root.
strict=false
case "${1:-}" in
  --strict) strict=true ;;
  "") ;;
  *) echo "usage: link-native-profiles.sh [--strict]" >&2; exit 2 ;;
esac
conflict() {
  echo "$1" >&2
  if [[ "$strict" == true ]]; then exit 1; fi
  exit 0
}
root="${HERMES_ROOT_PROFILE:-${RUNTIME_HERMES_HOME:-${HERMES_HOME:-}}}"
profiles="${HERMES_PROFILES_ROOT:-${RUNTIME_HERMES_PROFILES_ROOT:-}}"
[[ -n "$root" && -n "$profiles" ]] || conflict "Runtime profile roots are not configured"
native="$root/profiles"
[[ "$native" != "$profiles" ]] || exit 0

mkdir -p "$root" "$profiles"
# Serialize empty-directory replacement across simultaneous CLI invocations.
exec 8>"$root/.profile-layout.lock"
flock -w 10 8
if [[ -L "$native" ]]; then
  [[ "$(readlink -f "$native")" == "$(readlink -f "$profiles")" ]] || \
    conflict "CLI profiles link points outside the Runtime profiles root"
  exit 0
fi
native_resolved="$(readlink -m "$native")"
profiles_resolved="$(readlink -f "$profiles")"
[[ "$profiles_resolved" != "$(readlink -f "$root")" ]] || \
  conflict "Runtime profiles root must differ from the default profile"
[[ "$native_resolved" != "$profiles_resolved" ]] || exit 0
case "$profiles_resolved/" in
  "$native_resolved/"*) conflict "Runtime profiles root is nested inside the CLI profiles directory" ;;
esac
if [[ -d "$native" ]]; then
  # Existing nested profiles require an explicit, reviewed migration. Never
  # move or overwrite user profile data during container startup or a CLI run.
  if [[ -n "$(find "$native" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
    conflict "CLI profiles directory contains existing data; explicit migration required"
  fi
  rmdir "$native"
elif [[ -e "$native" ]]; then
  conflict "CLI profiles path is occupied"
fi
ln -sT "$profiles_resolved" "$native"
