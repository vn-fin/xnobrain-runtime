#!/usr/bin/env bash
set -euo pipefail

mkdir -p "$HOME" "$XDG_CONFIG_HOME" "$HERMES_HOME" "$HERMES_PROFILES_ROOT" "$NINE_ROUTER_DATA_DIR"
# Hermes CLI discovers named profiles at HERMES_HOME/profiles. Open Lumora's
# stable data contract keeps them at DATA_DIR/profiles, so expose that one
# directory through a compatibility symlink instead of duplicating state.
if [[ ! -e "$HERMES_HOME/profiles" ]]; then
  ln -s "$HERMES_PROFILES_ROOT" "$HERMES_HOME/profiles"
fi
/usr/local/bin/open-lumora-prepare-nine-router-auth
IFS= read -r NINE_ROUTER_API_KEY <"$NINE_ROUTER_DATA_DIR/auth/cli-token"
export NINE_ROUTER_API_KEY
touch "$HERMES_HOME/.env"
chmod 700 "$HERMES_HOME" "$HERMES_PROFILES_ROOT" "$NINE_ROUTER_DATA_DIR"
chmod 600 "$HERMES_HOME/.env"

# Keep the persistent default and named profiles in sync with the skills bundled
# by the Hermes runtime image. The upstream synchronizer is manifest-based: it
# preserves local edits and deletions and respects .no-bundled-skills markers.
sync_profile_skills() {
  local profile_dir="$1"
  HERMES_HOME="$profile_dir" /opt/hermes/.venv/bin/python -c \
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
node /opt/open-lumora/9router/server.js &
router_pid=$!
python3 /opt/open-lumora/server.py &
api_pid=$!

cleanup() {
  kill "$router_pid" 2>/dev/null || true
  kill "$api_pid" 2>/dev/null || true
  wait "$router_pid" "$api_pid" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

wait -n "$router_pid" "$api_pid"
