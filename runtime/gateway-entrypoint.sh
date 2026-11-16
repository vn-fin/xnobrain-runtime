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

# Incus containers publish only RUNTIME_* through the preparation unit while the
# unit file supplies the legacy Docker layout. Derive HERMES_* from the same
# canonical contract as the Runtime launcher so both processes share one profile
# root. Compose and the native VM unit already provide HERMES_* directly.
if [ -n "${RUNTIME_AGENT_DATA_ROOT:-}" ]; then
  canonical_root="${RUNTIME_AGENT_DATA_ROOT%/}/big-brother"
  for old_root in "${RUNTIME_HERMES_HOME:-}" "${RUNTIME_HERMES_ROOT_PROFILE:-}"; do
    if [ -n "$old_root" ] && [ "$old_root" != "$canonical_root" ]; then
      echo "Conflicting profile roots; offline migration required" >&2
      exit 1
    fi
  done
  if [ -n "${RUNTIME_HERMES_PROFILES_ROOT:-}" ] \
    && [ "${RUNTIME_HERMES_PROFILES_ROOT}" != "${RUNTIME_AGENT_DATA_ROOT%/}" ]; then
    echo "Conflicting profiles root; offline migration required" >&2
    exit 1
  fi
  HERMES_HOME="$canonical_root"
  HERMES_ROOT_PROFILE="$canonical_root"
  HERMES_PROFILES_ROOT="${RUNTIME_AGENT_DATA_ROOT%/}"
  export HERMES_HOME HERMES_ROOT_PROFILE HERMES_PROFILES_ROOT
elif [ -n "${RUNTIME_HERMES_HOME:-}" ]; then
  HERMES_HOME="$RUNTIME_HERMES_HOME"
  HERMES_ROOT_PROFILE="${RUNTIME_HERMES_ROOT_PROFILE:-$HERMES_HOME}"
  HERMES_PROFILES_ROOT="${RUNTIME_HERMES_PROFILES_ROOT:-$HERMES_HOME/profiles}"
  export HERMES_HOME HERMES_ROOT_PROFILE HERMES_PROFILES_ROOT
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

bridge_dir="$app_dir/runtime/hermes-plugins/platforms/zalo/bridge"
# Source-development checkouts install bridge dependencies on demand. Managed
# images install them at build time and are read-only, so npm never runs here.
if [ -f "$bridge_dir/package-lock.json" ] \
  && [ ! -d "$bridge_dir/node_modules/zca-js" ] \
  && [ -w "$bridge_dir" ] \
  && command -v npm >/dev/null 2>&1; then
  if ! (cd "$bridge_dir" && npm ci --omit=dev); then
    echo "Warning: Zalo bridge npm ci --omit=dev failed; continuing" >&2
  fi
fi

exec "${HERMES_RUNTIME_PYTHON:-/usr/local/lib/hermes-agent/venv/bin/python}" \
  -c 'from xnobrain.integrations.gateway_cli import main; main()'
