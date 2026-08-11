#!/usr/bin/env sh
set -eu

: "${NINE_ROUTER_DATA_DIR:?NINE_ROUTER_DATA_DIR is required}"
router_data_dir="$NINE_ROUTER_DATA_DIR"
mkdir -p "$router_data_dir"

DATA_DIR="$router_data_dir" \
PORT=20128 \
HOSTNAME=127.0.0.1 \
BASE_URL=http://127.0.0.1:20128 \
NEXT_PUBLIC_BASE_URL=http://127.0.0.1:20128 \
REQUIRE_API_KEY=false \
9router --host 127.0.0.1 --port 20128 --no-browser --skip-update &

exec /usr/local/bin/brain4all
