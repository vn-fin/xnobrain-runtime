#!/usr/bin/env bash
set -euo pipefail

# Validate operator paths before mkdir, template installation or skill sync.
hermes_python="${HERMES_RUNTIME_PYTHON:-/usr/local/lib/hermes-agent/venv/bin/python}"
if [[ ! -x "$hermes_python" ]]; then
  echo "XNOBrain runtime Python not found: $hermes_python" >&2
  exit 1
fi
if [[ -n "${RUNTIME_AGENT_DATA_ROOT:-}" ]]; then
  PYTHONPATH="/opt/xnobrain-app${PYTHONPATH:+:$PYTHONPATH}" "$hermes_python" -c \
    'import os; from xnobrain.agent_layout import resolve_layout; resolve_layout(os.environ)'
fi

# Canonical layout is opt-in for legacy installations; no automatic data moves.
if [[ -n "${RUNTIME_AGENT_DATA_ROOT:-}" ]]; then
  canonical_root="${RUNTIME_AGENT_DATA_ROOT%/}/big-brother"
  for old_root in "${RUNTIME_HERMES_HOME:-}" "${RUNTIME_HERMES_ROOT_PROFILE:-}"; do
    if [[ -n "$old_root" && "$old_root" != "$canonical_root" ]]; then
      echo "Conflicting profile roots; offline migration required" >&2
      exit 1
    fi
  done
  if [[ -n "${RUNTIME_HERMES_PROFILES_ROOT:-}" && "$RUNTIME_HERMES_PROFILES_ROOT" != "${RUNTIME_AGENT_DATA_ROOT%/}" ]]; then
    echo "Conflicting profiles root; offline migration required" >&2
    exit 1
  fi
  export HERMES_HOME="$canonical_root"
  export HERMES_ROOT_PROFILE="$canonical_root"
  export HERMES_PROFILES_ROOT="${RUNTIME_AGENT_DATA_ROOT%/}"
else
  HERMES_HOME="${RUNTIME_HERMES_HOME:-}"
  : "${HERMES_HOME:?RUNTIME_HERMES_HOME or RUNTIME_AGENT_DATA_ROOT is required}"
  export HERMES_HOME
  export HERMES_ROOT_PROFILE="${RUNTIME_HERMES_ROOT_PROFILE:-$HERMES_HOME}"
  export HERMES_PROFILES_ROOT="${RUNTIME_HERMES_PROFILES_ROOT:-$HERMES_HOME/profiles}"
fi
export XDG_CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
export API_SERVER_HOST="${RUNTIME_API_SERVER_HOST:-0.0.0.0}"
export API_SERVER_PORT="${RUNTIME_API_SERVER_PORT:-3000}"
export DATA_DIR="${RUNTIME_DATA_DIR:-/opt/data/xnobrain}"
export DEVELOPMENT_ENVIRONMENT="${RUNTIME_DEVELOPMENT_ENVIRONMENT:-development}"
export SERVICE_NAME="${RUNTIME_SERVICE_NAME:-xnobrain-runtime-services}"
export XNOBRAIN_PROFILE_TEMPLATE="${XNOBRAIN_PROFILE_TEMPLATE:-/opt/xnobrain/profile-templates}"
export OTEL_ENABLED="${RUNTIME_OTEL_TRACES_ENABLED:-false}"
export OTEL_EXPORTER_OTLP_ENDPOINT="${RUNTIME_OTEL_EXPORTER_OTLP_ENDPOINT:-}"
export OTEL_EXPORTER_OTLP_INSECURE="${RUNTIME_OTEL_EXPORTER_OTLP_INSECURE:-true}"
export RUNTIME_LLM_ROUTER_URL="${RUNTIME_LLM_ROUTER_URL:-}"
: "${RUNTIME_LLM_ROUTER_URL:?RUNTIME_LLM_ROUTER_URL is required}"
# A scoped workload key is provisioned by Control. Keep the container alive if
# provisioning has not supplied one yet so health/reconciliation can repair the
# assignment; inference remains unauthorized until the scoped key is injected.
export RUNTIME_LLM_API_KEY="${RUNTIME_LLM_API_KEY:-}"
export RUNTIME_ACCOUNTING_MODE="${RUNTIME_ACCOUNTING_MODE:-legacy}"
export RUNTIME_CONTROL_URL="${RUNTIME_CONTROL_URL:-}"

mkdir -p "$HOME" "$XDG_CONFIG_HOME" "$HERMES_HOME" "$HERMES_PROFILES_ROOT"
# Profile environments share persistent, quota-accounted data storage, not the
# instance root disk. Run as the same identity as the agent; never chmod 777.
install -d -m 0700 /opt/data/python
[[ -w /opt/data/python ]] || { echo "Python profile root is not writable" >&2; exit 1; }
hermes_python="${HERMES_RUNTIME_PYTHON:-/usr/local/lib/hermes-agent/venv/bin/python}"
if [[ ! -x "$hermes_python" ]]; then
  echo "XNOBrain runtime Python not found: $hermes_python" >&2
  exit 1
fi
# Hermes keeps its updater under HERMES_HOME/bin. The image provides uv as a
# system command, so make it available to every persistent profile root too.
mkdir -p "$HERMES_HOME/bin"
if [[ ! -e "$HERMES_HOME/bin/uv" ]]; then
  ln -s /usr/local/bin/uv "$HERMES_HOME/bin/uv"
fi
# Preserve compatibility for deployments that explicitly place named profiles
# outside the normal ~/.hermes/profiles location.
if [[ -z "${RUNTIME_AGENT_DATA_ROOT:-}" && "$HERMES_PROFILES_ROOT" != "$HERMES_HOME/profiles" && ! -e "$HERMES_HOME/profiles" ]]; then
  ln -s "$HERMES_PROFILES_ROOT" "$HERMES_HOME/profiles"
fi
touch "$HERMES_HOME/.env"
chmod 700 "$HERMES_HOME" "$HERMES_PROFILES_ROOT"
chmod 600 "$HERMES_HOME/.env"

XNOBRAIN_PROFILE_TEMPLATES_DIR=/opt/xnobrain/profile-templates \
XNOBRAIN_REQUIRED_SKILLS_DIR=/opt/xnobrain/required-skills \
XNOBRAIN_REQUIRED_PLUGINS_DIR=/opt/xnobrain/required-plugins \
  /usr/local/bin/xnobrain-apply-profile-templates "$HERMES_HOME" "$HERMES_PROFILES_ROOT"

# Keep the persistent default and named profiles in sync with the skills bundled
# by the Hermes runtime image. The upstream synchronizer is manifest-based: it
# preserves local edits and deletions and respects .no-bundled-skills markers.
sync_profile_skills() {
  local profile_dir="$1"
  HERMES_HOME="$profile_dir" "$hermes_python" -c \
    'from tools.skills_sync import sync_skills; sync_skills(quiet=True)'
}
sync_profile_skills "$HERMES_HOME"
for profile_dir in "$HERMES_PROFILES_ROOT"/*; do
  [[ -d "$profile_dir" && "$profile_dir" != "$HERMES_HOME" ]] || continue
  sync_profile_skills "$profile_dir"
done

# Skill synchronization may have installed the bundled PDF helper after the
# initial template pass. Reapply XNOBrain's link-preserving helper now.
XNOBRAIN_PROFILE_TEMPLATES_DIR=/opt/xnobrain/profile-templates \
XNOBRAIN_SKILL_OVERRIDES_DIR=/opt/xnobrain/skill-overrides \
XNOBRAIN_REQUIRED_SKILLS_DIR=/opt/xnobrain/required-skills \
XNOBRAIN_REQUIRED_PLUGINS_DIR=/opt/xnobrain/required-plugins \
  /usr/local/bin/xnobrain-apply-profile-templates "$HERMES_HOME" "$HERMES_PROFILES_ROOT"

exec "$hermes_python" /opt/xnobrain-app/server.py
