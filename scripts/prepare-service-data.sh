#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
python_bin="$project_dir/.tools/python/bin/python"
service_home="${RUNTIME_HOME:-/srv/xnobrain-data/home}"
: "${RUNTIME_HERMES_HOME:?RUNTIME_HERMES_HOME is required in the service environment file}"
: "${RUNTIME_OMNIROUTE_DATA_DIR:?RUNTIME_OMNIROUTE_DATA_DIR is required in the service environment file}"
hermes_home="$RUNTIME_HERMES_HOME"
profiles_root="${RUNTIME_HERMES_PROFILES_ROOT:-$hermes_home/profiles}"
router_data_dir="$RUNTIME_OMNIROUTE_DATA_DIR"
lock_file="$router_data_dir/.prepare.lock"

if [[ ! -x "$python_bin" ]]; then
  echo "XNOBrain Python runtime not found: $python_bin" >&2
  exit 1
fi

mkdir -p \
  "$hermes_home" \
  "$profiles_root" \
  "$router_data_dir/auth" \
  "${RUNTIME_DATA_DIR:-/srv/xnobrain-data/xnobrain}" \
  "$service_home"

exec 9>"$lock_file"
flock 9

bash "$project_dir/scripts/apply-profile-templates.sh" "$hermes_home" "$profiles_root"

machine_id="$(cat /etc/machine-id 2>/dev/null || hostname)"
printf '%s' "$machine_id" >"$router_data_dir/machine-id"
printf '%s' "${RUNTIME_OMNIROUTE_CLI_SALT:-omniroute-cli-auth-v1}" >"$router_data_dir/auth/cli-secret"
"$python_bin" - "$router_data_dir" <<'PY'
import hashlib
import hmac
from pathlib import Path
import sys

root = Path(sys.argv[1])
machine = (root / "machine-id").read_text().strip()
secret = (root / "auth" / "cli-secret").read_text().strip()
(root / "auth" / "cli-token").write_text(
    hmac.new(machine.encode(), secret.encode(), hashlib.sha256).hexdigest()
)
PY

chmod 700 "$hermes_home" "$profiles_root" "$router_data_dir" "$router_data_dir/auth"
chmod 600 \
  "$router_data_dir/machine-id" \
  "$router_data_dir/auth/cli-secret" \
  "$router_data_dir/auth/cli-token"

if [[ "${EUID}" -eq 0 && -n "${RUNTIME_SERVICE_USER:-}" ]]; then
  chown -R \
    "$RUNTIME_SERVICE_USER:$RUNTIME_SERVICE_USER" \
    "$service_home" \
    "$hermes_home" \
    "$profiles_root" \
    "$router_data_dir" \
    "${RUNTIME_DATA_DIR:-/srv/xnobrain-data/xnobrain}"
fi
