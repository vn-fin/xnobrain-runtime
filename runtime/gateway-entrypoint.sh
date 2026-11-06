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

# The systemd EnvironmentFile carries RUNTIME_* names and PID 1's HOME/PATH.
# Hermes needs HERMES_*; derive them from the same canonical layout as
# runtime/container-entrypoint.sh when the caller did not provide them.
if [ -z "${HERMES_HOME:-}" ]; then
  if [ -n "${RUNTIME_AGENT_DATA_ROOT:-}" ]; then
    HERMES_HOME="${RUNTIME_AGENT_DATA_ROOT%/}/big-brother"
    HERMES_ROOT_PROFILE="$HERMES_HOME"
    HERMES_PROFILES_ROOT="${RUNTIME_AGENT_DATA_ROOT%/}"
  elif [ -n "${RUNTIME_HERMES_HOME:-}" ]; then
    HERMES_HOME="$RUNTIME_HERMES_HOME"
    HERMES_ROOT_PROFILE="${RUNTIME_HERMES_ROOT_PROFILE:-$HERMES_HOME}"
    HERMES_PROFILES_ROOT="${RUNTIME_HERMES_PROFILES_ROOT:-$HERMES_HOME/profiles}"
  fi
fi
if [ -n "${HERMES_HOME:-}" ]; then
  export HERMES_HOME
fi
if [ -n "${HERMES_ROOT_PROFILE:-}" ]; then
  export HERMES_ROOT_PROFILE
fi
if [ -n "${HERMES_PROFILES_ROOT:-}" ]; then
  export HERMES_PROFILES_ROOT
fi

exec hermes gateway run
