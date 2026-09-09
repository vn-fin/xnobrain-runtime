#!/usr/bin/env bash
set -euo pipefail

HERMES_HOME="${RUNTIME_HERMES_HOME:-}"
: "${HERMES_HOME:?RUNTIME_HERMES_HOME is required}"
export HERMES_HOME
export HERMES_ROOT_PROFILE="${RUNTIME_HERMES_ROOT_PROFILE:-$HERMES_HOME}"
export HERMES_PROFILES_ROOT="${RUNTIME_HERMES_PROFILES_ROOT:-$HERMES_HOME/profiles}"
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

mkdir -p "$HOME" "$XDG_CONFIG_HOME" "$HERMES_HOME" "$HERMES_PROFILES_ROOT"
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
if [[ "$HERMES_PROFILES_ROOT" != "$HERMES_HOME/profiles" && ! -e "$HERMES_HOME/profiles" ]]; then
  ln -s "$HERMES_PROFILES_ROOT" "$HERMES_HOME/profiles"
fi
touch "$HERMES_HOME/.env"
chmod 700 "$HERMES_HOME" "$HERMES_PROFILES_ROOT"
chmod 600 "$HERMES_HOME/.env"

XNOBRAIN_PROFILE_TEMPLATES_DIR=/opt/xnobrain/profile-templates \
XNOBRAIN_REQUIRED_SKILLS_DIR=/opt/xnobrain/required-skills \
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
  [[ -d "$profile_dir" ]] || continue
  sync_profile_skills "$profile_dir"
done

# Skill synchronization may have installed the bundled PDF helper after the
# initial template pass. Reapply XNOBrain's link-preserving helper now.
XNOBRAIN_PROFILE_TEMPLATES_DIR=/opt/xnobrain/profile-templates \
XNOBRAIN_SKILL_OVERRIDES_DIR=/opt/xnobrain/skill-overrides \
XNOBRAIN_REQUIRED_SKILLS_DIR=/opt/xnobrain/required-skills \
  /usr/local/bin/xnobrain-apply-profile-templates "$HERMES_HOME" "$HERMES_PROFILES_ROOT"

if [[ "${XNOBRAIN_BUILD_MODE:-source}" == "source" ]]; then
  exec "$hermes_python" /opt/xnobrain-app/server.py
fi
exec /usr/local/bin/app.so
