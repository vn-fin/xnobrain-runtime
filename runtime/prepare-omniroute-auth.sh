#!/usr/bin/env bash
# Prepare OmniRoute's local CLI identity shared with the XNOBrain adapter.
set -euo pipefail

: "${OMNIROUTE_DATA_DIR:?OMNIROUTE_DATA_DIR is required}"
data_directory="$OMNIROUTE_DATA_DIR"
mkdir -p "$data_directory/auth"
umask 077
printf '%s' "$(cat /etc/machine-id 2>/dev/null || hostname)" >"$data_directory/machine-id"
printf '%s' "${OMNIROUTE_CLI_SALT:-omniroute-cli-auth-v1}" >"$data_directory/auth/cli-secret"
python3 -c 'import hashlib, hmac, pathlib, sys; root=pathlib.Path(sys.argv[1]); machine=(root/"machine-id").read_text().strip(); salt=(root/"auth/cli-secret").read_text().strip(); print(hmac.new(machine.encode(), salt.encode(), hashlib.sha256).hexdigest())' "$data_directory" >"$data_directory/auth/cli-token"
chmod 600 "$data_directory/machine-id" "$data_directory/auth/cli-secret" "$data_directory/auth/cli-token"
