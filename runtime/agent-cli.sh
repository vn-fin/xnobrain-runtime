#!/usr/bin/env bash
set -euo pipefail

# Native profile commands run in a separate process. Resolve the workspace's
# current Router key at invocation time, without persisting it in a profile.
/usr/local/bin/xnobrain-link-native-profiles
key_file="${RUNTIME_LLM_API_KEY_FILE:-}"
if [[ -n "$key_file" && -f "$key_file" && ! -L "$key_file" && -r "$key_file" ]]; then
  RUNTIME_LLM_API_KEY="$(cat "$key_file")"
  export RUNTIME_LLM_API_KEY
elif [[ -n "$key_file" ]]; then
  # A configured file is authoritative; never fall back to a stale env key.
  RUNTIME_LLM_API_KEY=""
  export RUNTIME_LLM_API_KEY
fi

exec /usr/local/lib/hermes-agent/venv/bin/hermes "$@"
