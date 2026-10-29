#!/usr/bin/env bash
set -euo pipefail

# The root preparation unit validates storage and provides EnvironmentFile.
# Agent processes never need permission to read PID 1's environment.
exec /usr/local/bin/xnobrain-runtime
