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

backend_host=0.0.0.0
backend_port=3000

hermes_home="${RUNTIME_HERMES_HOME:-}"
: "${hermes_home:?RUNTIME_HERMES_HOME is required. Set it in .env}"
: "${RUNTIME_LLM_GATEWAY_URL:?RUNTIME_LLM_GATEWAY_URL is required. Set it in .env}"
: "${RUNTIME_LLM_MANAGEMENT_URL:?RUNTIME_LLM_MANAGEMENT_URL is required. Set it in .env}"
: "${RUNTIME_LLM_WORKLOAD_TOKEN:?RUNTIME_LLM_WORKLOAD_TOKEN is required. Set it in .env}"
backend_pid=""

cleanup() {
  trap - EXIT INT TERM
  if [[ -n "$backend_pid" ]]; then
    kill "$backend_pid" 2>/dev/null || true
    wait "$backend_pid" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

cd "$project_dir"
HERMES_HOME="$hermes_home" \
  HERMES_ROOT_PROFILE="${HERMES_ROOT_PROFILE:-$hermes_home}" \
  HERMES_PROFILES_ROOT="${HERMES_PROFILES_ROOT:-$hermes_home/profiles}" \
  OMNIROUTE_API_KEY="$RUNTIME_LLM_WORKLOAD_TOKEN" \
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
