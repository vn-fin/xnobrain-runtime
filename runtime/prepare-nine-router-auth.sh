#!/usr/bin/env bash
# Prepare the private 9router CLI identity shared with Open Lumora Studio.
set -euo pipefail

data_directory="${NINE_ROUTER_DATA_DIR:-/opt/data/.9router}"
mkdir -p "$data_directory/auth"
umask 077
printf '%s' 'open-lumora-runtime' >"$data_directory/machine-id"
printf '%s' "${NINE_ROUTER_INTERNAL_SECRET:-open-lumora-local-9router}" >"$data_directory/auth/cli-secret"
python3 -c 'import hashlib, pathlib, sys; root=pathlib.Path(sys.argv[1]); machine=(root/"machine-id").read_text().strip(); secret=(root/"auth/cli-secret").read_text().strip(); print(hashlib.sha256(f"{machine}9r-cli-auth{secret}".encode()).hexdigest()[:16])' "$data_directory" >"$data_directory/auth/cli-token"
chmod 600 "$data_directory/machine-id" "$data_directory/auth/cli-secret" "$data_directory/auth/cli-token"
