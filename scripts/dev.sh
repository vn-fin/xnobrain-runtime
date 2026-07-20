#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
go_bin="go"
if [[ -x "$project_dir/.tools/go/bin/go" ]]; then
  go_bin="$project_dir/.tools/go/bin/go"
fi

cd "$project_dir/frontend"
npm install
npm run dev -- --host 127.0.0.1 &
frontend_pid=$!

cleanup() {
  kill "$frontend_pid" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

cd "$project_dir"
"$go_bin" run cmd/main.go
