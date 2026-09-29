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

# Check storage before mkdir, templates, ownership changes or profile access.
PYTHONPATH="$project_dir${PYTHONPATH:+:$PYTHONPATH}" "$python_bin" -m xnobrain.integrations.runtime_data_volume

# Do not reseed an initialized persistent volume on ordinary service restarts.
if [[ "${RUNTIME_DATA_MOUNT_REQUIRED:-false}" == true && -f "${RUNTIME_DATA_VOLUME_PATH}/.xnobrain-volume-initialized" ]]; then
  test -d "$hermes_home"
  test -d "$profiles_root"
  test -d "${RUNTIME_DATA_DIR}"
  exit 0
fi

# A deployment candidate already contains verified, preserved user data. Keep
# template/profile preparation from changing it before Runtime post-verification.
if [[ -f /etc/xnobrain/rollout-preserve-data ]]; then
  test -d "$hermes_home"
  test -d "$profiles_root"
  test -d "${RUNTIME_DATA_DIR:-/srv/xnobrain-data/xnobrain}"
  exit 0
fi

mkdir -p \
  "$hermes_home" \
  "$profiles_root" \
  "${RUNTIME_DATA_DIR:-/srv/xnobrain-data/xnobrain}" \
  "$service_home"

exec 9>"$lock_file"
flock 9

HERMES_ROOT_PROFILE="$hermes_home" HERMES_PROFILES_ROOT="$profiles_root" \
  bash "$project_dir/runtime/link-native-profiles.sh"

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

if [[ "${RUNTIME_DATA_MOUNT_REQUIRED:-false}" == true ]]; then
  touch "${RUNTIME_DATA_VOLUME_PATH}/.xnobrain-volume-initialized"
  sync -f "${RUNTIME_DATA_VOLUME_PATH}"
fi
