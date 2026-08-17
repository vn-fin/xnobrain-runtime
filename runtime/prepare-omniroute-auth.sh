#!/usr/bin/env bash
# Prepare OmniRoute's local CLI identity shared with the XNOBrain adapter.
set -euo pipefail

: "${OMNIROUTE_DATA_DIR:?OMNIROUTE_DATA_DIR is required}"
data_directory="$OMNIROUTE_DATA_DIR"
mkdir -p "$data_directory/auth"
umask 077
printf '%s' "$(cat /etc/machine-id 2>/dev/null || hostname)" >"$data_directory/machine-id"
printf '%s' "${OMNIROUTE_CLI_SALT:-omniroute-cli-auth-v1}" >"$data_directory/auth/cli-secret"
python3 - "$data_directory" <<'PY'
import hashlib
import hmac
from pathlib import Path
import sqlite3
import sys

root = Path(sys.argv[1])
machine = (root / "machine-id").read_text().strip()
salt = (root / "auth" / "cli-secret").read_text().strip()
(root / "auth" / "cli-token").write_text(
    hmac.new(machine.encode(), salt.encode(), hashlib.sha256).hexdigest()
)

# XNOBrain owns this private loopback-only OmniRoute instance. Disable the
# dashboard login guard so its internal provider-management API stays usable
# even when a persistent volume was previously initialized with a password.
database = root / "storage.sqlite"
if database.is_file():
    with sqlite3.connect(database) as connection:
        has_settings = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'key_value'"
        ).fetchone()
        if has_settings:
            connection.execute(
                "UPDATE key_value SET value = 'false' "
                "WHERE namespace = 'settings' AND key = 'requireLogin'"
            )
PY
chmod 600 "$data_directory/machine-id" "$data_directory/auth/cli-secret" "$data_directory/auth/cli-token"
