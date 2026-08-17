#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
hermes_source_dir="$project_dir/.tools/hermes-agent"
if [[ -d "$hermes_source_dir/hermes_cli" ]]; then
  export PYTHONPATH="$hermes_source_dir${PYTHONPATH:+:$PYTHONPATH}"
fi

python_bin="python3"
for candidate in \
  "$project_dir/.tools/python/bin/python" \
  "$HOME/.local/lib/hermes-agent/venv/bin/python" \
  "$(command -v python3 2>/dev/null || true)"; do
  if [[ -x "$candidate" ]] \
      && "$candidate" -c 'import hermes_cli, jlogger' >/dev/null 2>&1; then
    python_bin="$candidate"
    break
  fi
done

if ! "$python_bin" -c 'import hermes_cli, jlogger' >/dev/null 2>&1; then
  echo "The project Python environment is incomplete (Hermes or XNOBrain dependencies are missing)." >&2
  if [[ "$(uname -s)" == "Darwin" ]]; then
    echo "Repair the macOS project venv with:" >&2
    echo "  .tools/python/bin/python -m pip install --force-reinstall --no-deps -e .tools/hermes-agent --config-settings editable_mode=compat" >&2
    echo "  .tools/python/bin/python -m pip install -r requirements.txt" >&2
  else
    echo "Run ./scripts/install-linux.sh to repair the local toolchain." >&2
  fi
  exit 1
fi

node_bin_dir=""
if [[ -x "$project_dir/.tools/node/bin/node" ]]; then
  node_bin_dir="$project_dir/.tools/node/bin"
fi
npm_global_bin="$project_dir/.tools/npm-global/bin"
hermes_bin="$project_dir/.tools/hermes-agent/venv/bin"
office_bin="$project_dir/.tools/office-python/bin"
if [[ -n "$node_bin_dir" ]]; then
  export PATH="$node_bin_dir:$npm_global_bin:$hermes_bin:$office_bin:$PATH"
else
  export PATH="$npm_global_bin:$hermes_bin:$office_bin:$PATH"
fi

router_bin=""
for candidate in \
  "$npm_global_bin/9router" \
  "$(command -v 9router 2>/dev/null || true)"; do
  if [[ -x "$candidate" ]]; then
    router_bin="$candidate"
    break
  fi
done
backend_host="${XNOBRAIN_DEV_API_HOST:-0.0.0.0}"
backend_port="${XNOBRAIN_DEV_API_PORT:-8642}"
router_host="${XNOBRAIN_DEV_ROUTER_HOST:-127.0.0.1}"
router_port="${XNOBRAIN_DEV_ROUTER_PORT:-20128}"
router_url="${NINE_ROUTER_URL:-http://$router_host:$router_port}"

router_is_running() {
  "$python_bin" - "$router_url" <<'PY'
import sys
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

try:
    urlopen(sys.argv[1].rstrip("/") + "/api/providers", timeout=1).close()
except HTTPError:
    # An HTTP response, including an authentication error, proves that the
    # existing 9router process is reachable.
    raise SystemExit(0)
except (OSError, URLError):
    raise SystemExit(1)
PY
}

router_already_running=false
if router_is_running; then
  router_already_running=true
  echo "Reusing existing 9router at $router_url"
elif [[ -z "$router_bin" && "${XNOBRAIN_DEV_SKIP_ROUTER:-0}" != "1" ]]; then
  echo "9router is not running or installed. Run ./scripts/install-linux.sh first, or set XNOBRAIN_DEV_SKIP_ROUTER=1 for API-only work." >&2
  exit 1
fi

hermes_home="${XNOBRAIN_AGENT_HOME:-${HERMES_HOME:-}}"
router_data_dir="${XNOBRAIN_PROVIDER_DATA_DIR:-${NINE_ROUTER_DATA_DIR:-}}"
: "${hermes_home:?XNOBRAIN_AGENT_HOME is required. Set it in .env}"
: "${router_data_dir:?XNOBRAIN_PROVIDER_DATA_DIR is required. Set it in .env}"
backend_pid=""
router_pid=""

mkdir -p "$router_data_dir/auth"

prepare_router_auth() {
  if [[ ! -s "$router_data_dir/machine-id" ]]; then
    printf '%s' "$(cat /etc/machine-id 2>/dev/null || hostname)" > "$router_data_dir/machine-id"
  fi
  if [[ ! -s "$router_data_dir/auth/cli-secret" ]]; then
    "$python_bin" -c 'import secrets; print(secrets.token_hex(32), end="")' > "$router_data_dir/auth/cli-secret"
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
  chmod 700 "$router_data_dir" "$router_data_dir/auth"
  chmod 600 "$router_data_dir/machine-id" "$router_data_dir/auth/cli-secret" "$router_data_dir/auth/cli-token"
}

cleanup() {
  trap - EXIT INT TERM
  if [[ -n "$router_pid" ]]; then
    kill "$router_pid" 2>/dev/null || true
    wait "$router_pid" 2>/dev/null || true
  fi
  if [[ -n "$backend_pid" ]]; then
    kill "$backend_pid" 2>/dev/null || true
    wait "$backend_pid" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

cd "$project_dir"
if [[ "$router_already_running" == true || -n "$router_bin" ]]; then
  prepare_router_auth
  NINE_ROUTER_API_KEY="$(< "$router_data_dir/auth/cli-token")"
  : "${NINE_ROUTER_API_KEY:?9router CLI token is empty}"
  export NINE_ROUTER_API_KEY
fi
if [[ "$router_already_running" == false && -n "$router_bin" ]]; then
  DATA_DIR="$router_data_dir" \
    PORT="$router_port" \
    HOSTNAME=0.0.0.0 \
    BASE_URL="$router_url" \
    NEXT_PUBLIC_BASE_URL="$router_url" \
    REQUIRE_API_KEY=false \
    NODE_ENV=development \
    "$router_bin" --host "$router_host" --port "$router_port" --no-browser --skip-update &
  router_pid=$!
fi

HERMES_HOME="$hermes_home" \
  HERMES_ROOT_PROFILE="${HERMES_ROOT_PROFILE:-$hermes_home}" \
  HERMES_PROFILES_ROOT="${HERMES_PROFILES_ROOT:-$hermes_home/profiles}" \
  NINE_ROUTER_DATA_DIR="$router_data_dir" \
  NINE_ROUTER_URL="$router_url" \
  HERMES_CLI="${HERMES_CLI:-$npm_global_bin/agent}" \
  HERMES_SERVE_HEADLESS=1 \
  BROWSER=/bin/false \
  DISPLAY= \
  WAYLAND_DISPLAY= \
  XNOBRAIN_RELOAD=0 \
  API_SERVER_HOST="$backend_host" \
  API_SERVER_PORT="$backend_port" \
  "$python_bin" server.py &
backend_pid=$!

wait
