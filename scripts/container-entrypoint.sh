#!/usr/bin/env sh
set -eu

: "${OMNIROUTE_DATA_DIR:?OMNIROUTE_DATA_DIR is required}"
router_data_dir="$OMNIROUTE_DATA_DIR"
mkdir -p "$router_data_dir"

DATA_DIR="$router_data_dir" \
PORT=20128 \
HOSTNAME=127.0.0.1 \
API_PORT=20128 \
DASHBOARD_PORT=20128 \
REQUIRE_API_KEY=false \
OMNIROUTE_NO_UPDATE_NOTIFIER=1 \
omniroute serve --port 20128 --no-open &

exec env HERMES_SERVE_HEADLESS=1 BROWSER=/bin/false DISPLAY= WAYLAND_DISPLAY= /usr/local/bin/xnobrain
