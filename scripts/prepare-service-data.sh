#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
python_bin="$project_dir/.tools/python/bin/python"
service_home="${HOME:-/srv/brain4all-data/home}"
hermes_home="${HERMES_HOME:-$service_home/.hermes}"
profiles_root="${HERMES_PROFILES_ROOT:-$hermes_home/profiles}"
router_data_dir="${NINE_ROUTER_DATA_DIR:-$service_home/.9router}"
lock_file="$router_data_dir/.prepare.lock"

if [[ ! -x "$python_bin" ]]; then
  echo "Brain4All Python runtime not found: $python_bin" >&2
  exit 1
fi

mkdir -p \
  "$hermes_home" \
  "$profiles_root" \
  "$router_data_dir/auth" \
  "${DATA_DIR:-/srv/brain4all-data/brain4all}" \
  "$service_home"

exec 9>"$lock_file"
flock 9

bash "$project_dir/scripts/apply-profile-templates.sh" "$hermes_home" "$profiles_root"

if [[ ! -s "$router_data_dir/machine-id" ]]; then
  machine_id="$(cat /etc/machine-id 2>/dev/null || hostname)"
  printf '%s' "$machine_id" >"$router_data_dir/machine-id"
fi
if [[ ! -s "$router_data_dir/auth/cli-secret" ]]; then
  "$python_bin" -c 'import secrets; print(secrets.token_hex(32), end="")' \
    >"$router_data_dir/auth/cli-secret"
fi
"$python_bin" - "$router_data_dir" <<'PY'
import hashlib
from pathlib import Path
import sys

root = Path(sys.argv[1])
machine = (root / "machine-id").read_text().strip()
secret = (root / "auth" / "cli-secret").read_text().strip()
(root / "auth" / "cli-token").write_text(
    hashlib.sha256(f"{machine}9r-cli-auth{secret}".encode()).hexdigest()[:16]
)
PY

chmod 700 "$hermes_home" "$profiles_root" "$router_data_dir" "$router_data_dir/auth"
chmod 600 \
  "$router_data_dir/machine-id" \
  "$router_data_dir/auth/cli-secret" \
  "$router_data_dir/auth/cli-token"
