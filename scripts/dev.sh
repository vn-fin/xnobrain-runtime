#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
python_bin="python3"
if [[ -x "$project_dir/.tools/python/bin/python" ]]; then
  python_bin="$project_dir/.tools/python/bin/python"
elif [[ -x "$HOME/.local/lib/hermes-agent/venv/bin/python" ]]; then
  python_bin="$HOME/.local/lib/hermes-agent/venv/bin/python"
fi

cd "$project_dir/src"
npm install
npm run dev -- --host 127.0.0.1 &
frontend_pid=$!

cleanup() {
  kill "$frontend_pid" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

cd "$project_dir"
exec "$python_bin" server.py
