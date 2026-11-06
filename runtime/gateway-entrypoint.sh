#!/bin/sh
set -eu

# Hermes reads the router key from the environment variable named by the
# profile's key_env (RUNTIME_LLM_API_KEY). Control provisions it into a
# read-only file; load it without ever printing the value.
if [ -z "${RUNTIME_LLM_API_KEY:-}" ] \
  && [ -n "${RUNTIME_LLM_API_KEY_FILE:-}" ] \
  && [ -r "${RUNTIME_LLM_API_KEY_FILE}" ]; then
  RUNTIME_LLM_API_KEY="$(cat "$RUNTIME_LLM_API_KEY_FILE")"
  export RUNTIME_LLM_API_KEY
fi

# Under systemd the gateway shares the workspace with Runtime's own API port.
export RUNTIME_INTERNAL_URL="${RUNTIME_INTERNAL_URL:-http://127.0.0.1:${API_SERVER_PORT:-3000}}"

# The systemd unit and Compose provide HERMES_HOME and the profile roots.
# XNOBrain's launcher installs the workspace credential and attribution shims,
# then runs Hermes' own foreground `gateway run` path.
app_dir="${XNOBRAIN_APP_DIR:-}"
if [ -z "$app_dir" ]; then
  if [ -d /workspace/xnobrain ]; then
    # Source-development containers mount the checkout at /workspace.
    app_dir=/workspace
  else
    app_dir=/opt/xnobrain-app
  fi
fi
export PYTHONPATH="$app_dir${PYTHONPATH:+:$PYTHONPATH}"
cd "$app_dir"

exec "${HERMES_RUNTIME_PYTHON:-/usr/local/lib/hermes-agent/venv/bin/python}" \
  -c 'from xnobrain.integrations.gateway_cli import main; main()'
