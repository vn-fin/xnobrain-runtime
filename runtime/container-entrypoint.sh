#!/usr/bin/env bash
set -euo pipefail

mkdir -p "$HERMES_HOME" "$HERMES_PROFILES_ROOT" "$NINE_ROUTER_DATA_DIR"
/usr/local/bin/open-lumora-prepare-nine-router-auth
touch "$HERMES_HOME/.env"
chmod 700 "$HERMES_HOME" "$HERMES_PROFILES_ROOT" "$NINE_ROUTER_DATA_DIR"
chmod 600 "$HERMES_HOME/.env"

python3 - "$HERMES_HOME/.env" <<'PY'
from pathlib import Path
import os
import sys

path = Path(sys.argv[1])
current = {}
for line in path.read_text(encoding="utf-8").splitlines():
    if "=" in line and not line.lstrip().startswith("#"):
        key, value = line.split("=", 1)
        current[key] = value
for key in ("API_SERVER_ENABLED", "API_SERVER_HOST", "API_SERVER_PORT"):
    current[key] = os.environ[key]
if os.environ.get("HERMES_RUNTIME_TOKEN", "").strip():
    current["API_SERVER_KEY"] = os.environ["HERMES_RUNTIME_TOKEN"].strip()
path.write_text("".join(f"{key}={value}\n" for key, value in sorted(current.items())), encoding="utf-8")
PY

DATA_DIR="$NINE_ROUTER_DATA_DIR" \
PORT=20128 \
HOSTNAME=0.0.0.0 \
BASE_URL=http://127.0.0.1:20128 \
NEXT_PUBLIC_BASE_URL=http://127.0.0.1:20128 \
REQUIRE_API_KEY=false \
NODE_ENV=production \
node /opt/open-lumora/9router/server.js &
router_pid=$!
hermes-custom-gateway &
gateway_pid=$!

cleanup() {
  kill "$router_pid" 2>/dev/null || true
  kill "$gateway_pid" 2>/dev/null || true
  wait "$router_pid" "$gateway_pid" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

wait -n "$router_pid" "$gateway_pid"
