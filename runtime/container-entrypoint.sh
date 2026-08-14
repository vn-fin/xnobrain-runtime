#!/usr/bin/env bash
set -euo pipefail

: "${HERMES_HOME:?HERMES_HOME is required}"
: "${NINE_ROUTER_DATA_DIR:?NINE_ROUTER_DATA_DIR is required}"
export HERMES_HOME NINE_ROUTER_DATA_DIR
export HERMES_ROOT_PROFILE="${HERMES_ROOT_PROFILE:-$HERMES_HOME}"
export HERMES_PROFILES_ROOT="${HERMES_PROFILES_ROOT:-$HERMES_HOME/profiles}"
export XDG_CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"

mkdir -p "$HOME" "$XDG_CONFIG_HOME" "$HERMES_HOME" "$HERMES_PROFILES_ROOT" "$NINE_ROUTER_DATA_DIR"
hermes_python="${HERMES_RUNTIME_PYTHON:-/usr/local/lib/hermes-agent/venv/bin/python}"
if [[ ! -x "$hermes_python" ]]; then
  echo "Hermes runtime Python not found: $hermes_python" >&2
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
/usr/local/bin/xnobrain-prepare-nine-router-auth
NINE_ROUTER_API_KEY="$(< "$NINE_ROUTER_DATA_DIR/auth/cli-token")"
: "${NINE_ROUTER_API_KEY:?9router CLI token is empty}"
export NINE_ROUTER_API_KEY
touch "$HERMES_HOME/.env"
chmod 700 "$HERMES_HOME" "$HERMES_PROFILES_ROOT" "$NINE_ROUTER_DATA_DIR"
chmod 600 "$HERMES_HOME/.env"

XNOBRAIN_PROFILE_TEMPLATES_DIR=/opt/xnobrain/profile-templates \
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

DATA_DIR="$NINE_ROUTER_DATA_DIR" \
PORT=20128 \
HOSTNAME=0.0.0.0 \
BASE_URL=http://127.0.0.1:20128 \
NEXT_PUBLIC_BASE_URL=http://127.0.0.1:20128 \
REQUIRE_API_KEY=false \
NODE_ENV=production \
node /opt/xnobrain/9router/server.js &
router_pid=$!
"$hermes_python" /opt/xnobrain/server.py &
api_pid=$!

cleanup() {
  kill "$router_pid" 2>/dev/null || true
  kill "$api_pid" 2>/dev/null || true
  wait "$router_pid" "$api_pid" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

wait -n "$router_pid" "$api_pid"
