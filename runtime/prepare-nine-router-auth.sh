#!/usr/bin/env bash
# Prepare the private 9router CLI identity shared with Open Lumora Studio.
set -euo pipefail

data_directory="${NINE_ROUTER_DATA_DIR:-/opt/data/.9router}"
mkdir -p "$data_directory/auth"
umask 077
printf '%s' 'open-lumora-runtime' >"$data_directory/machine-id"
printf '%s' "${NINE_ROUTER_INTERNAL_SECRET:-open-lumora-local-9router}" >"$data_directory/auth/cli-secret"
