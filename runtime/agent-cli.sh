#!/usr/bin/env bash
set -euo pipefail

# Native profile commands run in a separate process. Resolve the workspace's
# current Router key at invocation time, without persisting it in a profile.
script_dir="$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}")")"
linker=/usr/local/bin/xnobrain-link-native-profiles
hermes=/usr/local/lib/hermes-agent/venv/bin/hermes
if [[ -f "$script_dir/link-native-profiles.sh" ]]; then
  # Native installation: agent is a symlink to this source-owned wrapper.
  linker="$script_dir/link-native-profiles.sh"
  hermes="$script_dir/../.tools/hermes-agent/venv/bin/hermes"
fi

# Hold a kernel activity lease before profile linking or any native command.
# Children inherit the same open descriptor; completion never relies on a PID.
if [[ -z "${XNOBRAIN_ACTIVITY_FD:-}" && -z "${RUNTIME_WORKSPACE_ID:-}" ]]; then
  exec "$(dirname "$hermes")/python" -m xnobrain.integrations.rebalance_cli bash "${BASH_SOURCE[0]}" "$@"
fi

export HERMES_ROOT_PROFILE="${RUNTIME_HERMES_ROOT_PROFILE:-${RUNTIME_HERMES_HOME:-${HERMES_ROOT_PROFILE:-${HERMES_HOME:-}}}}"
export HERMES_PROFILES_ROOT="${RUNTIME_HERMES_PROFILES_ROOT:-${HERMES_PROFILES_ROOT:-}}"

# Read-only commands remain available for diagnosing an existing layout
# conflict. Creating/importing/renaming must never continue into the wrong root.
link_args=()
previous=""
help=false
for argument in "$@"; do
  case "$argument" in -h|--help) help=true ;; esac
  if [[ "$previous" == profile ]]; then
    case "$argument" in create|import|rename) link_args=(--strict) ;; esac
  fi
  previous="$argument"
done
if [[ "$help" == true ]]; then link_args=(); fi
if [[ -n "$HERMES_PROFILES_ROOT" || -n "${RUNTIME_HERMES_HOME:-}" ]]; then
  if [[ -n "${RUNTIME_WORKSPACE_ID:-}" && -z "${XNOBRAIN_ACTIVITY_FD:-}" ]]; then
    "$(dirname "$hermes")/python" -m xnobrain.integrations.rebalance_cli bash "$linker" "${link_args[@]}"
  else
    bash "$linker" "${link_args[@]}"
  fi
fi

# The upstream CLI derives its root from HERMES_HOME, not PROFILES_ROOT.
# Anchor it at Runtime's root while preserving a selected named profile.
if [[ -n "$HERMES_ROOT_PROFILE" && -n "$HERMES_PROFILES_ROOT" ]]; then
  selected_home="$(readlink -m "${HERMES_HOME:-$HERMES_ROOT_PROFILE}")"
  root_home="$(readlink -m "$HERMES_ROOT_PROFILE")"
  profiles_home="$(readlink -m "$HERMES_PROFILES_ROOT")"
  if [[ "$selected_home" == "$root_home" ]]; then
    export HERMES_HOME="$HERMES_ROOT_PROFILE"
  elif [[ "$(dirname "$selected_home")" == "$profiles_home" ]]; then
    explicit_profile=false
    for argument in "$@"; do
      case "$argument" in -p|--profile|--profile=*|-p?*) explicit_profile=true; break ;; esac
    done
    if [[ "$explicit_profile" == false ]]; then
      set -- -p "$(basename "$selected_home")" "$@"
    fi
    export HERMES_HOME="$HERMES_ROOT_PROFILE"
  elif [[ ${#link_args[@]} -gt 0 ]]; then
    echo "CLI active profile is outside the Runtime profile layout" >&2
    exit 1
  fi
fi
key_file="${RUNTIME_LLM_API_KEY_FILE:-}"
if [[ -n "$key_file" && -f "$key_file" && ! -L "$key_file" && -r "$key_file" ]]; then
  RUNTIME_LLM_API_KEY="$(cat "$key_file")"
  export RUNTIME_LLM_API_KEY
elif [[ -n "$key_file" ]]; then
  # A configured file is authoritative; never fall back to a stale env key.
  RUNTIME_LLM_API_KEY=""
  export RUNTIME_LLM_API_KEY
fi

if [[ -n "${RUNTIME_WORKSPACE_ID:-}" ]]; then
  exec "$(dirname "$hermes")/python" -m xnobrain.integrations.capacity_cli "$@"
fi
exec "$hermes" "$@"
