#!/usr/bin/env bash
set -euo pipefail

# Incus injects instance environment into PID 1. systemd intentionally does not
# propagate that complete environment to services, so copy only the runtime
# contract variables required by the workspace process.
while IFS= read -r -d '' entry; do
  key="${entry%%=*}"
  case "$key" in
    HOME|PATH|XDG_CONFIG_HOME|HERMES_*|RUNTIME_*) export "$entry" ;;
  esac
done </proc/1/environ

exec /usr/local/bin/xnobrain-runtime
