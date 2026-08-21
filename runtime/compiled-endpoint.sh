#!/usr/bin/env bash
set -euo pipefail

export PYTHONPATH="/opt/xnobrain-compiled${PYTHONPATH:+:${PYTHONPATH}}"
exec "${HERMES_RUNTIME_PYTHON:-/usr/local/lib/hermes-agent/venv/bin/python}" \
  -c 'from xnobrain.server import main; main()'
