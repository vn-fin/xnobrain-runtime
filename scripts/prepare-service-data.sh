#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
python_bin="$project_dir/.tools/python/bin/python"
service_home="${RUNTIME_HOME:-/srv/xnobrain-data/home}"
: "${RUNTIME_HERMES_HOME:?RUNTIME_HERMES_HOME is required in the service environment file}"
hermes_home="$RUNTIME_HERMES_HOME"
profiles_root="${RUNTIME_HERMES_PROFILES_ROOT:-$hermes_home/profiles}"
lock_file="${RUNTIME_DATA_DIR:-/srv/xnobrain-data/xnobrain}/.prepare.lock"

if [[ ! -x "$python_bin" ]]; then
  echo "XNOBrain Python runtime not found: $python_bin" >&2
  exit 1
fi

mkdir -p \
  "$hermes_home" \
  "$profiles_root" \
  "${RUNTIME_DATA_DIR:-/srv/xnobrain-data/xnobrain}" \
  "$service_home"

exec 9>"$lock_file"
flock 9

bash "$project_dir/scripts/apply-profile-templates.sh" "$hermes_home" "$profiles_root"

chmod 700 "$hermes_home" "$profiles_root"

if [[ "${EUID}" -eq 0 && -n "${RUNTIME_SERVICE_USER:-}" ]]; then
  chown -R \
    "$RUNTIME_SERVICE_USER:$RUNTIME_SERVICE_USER" \
    "$service_home" \
    "$hermes_home" \
    "$profiles_root" \
    "${RUNTIME_DATA_DIR:-/srv/xnobrain-data/xnobrain}"
fi
