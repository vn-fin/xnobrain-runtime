#!/usr/bin/env bash
set -euo pipefail

app_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if ! xcode-select -p >/dev/null 2>&1; then
  xcode-select --install
  echo 'Finish the Apple Command Line Tools installer, then run make again.' >&2
  exit 2
fi
if ! command -v brew >/dev/null 2>&1; then
  echo 'Homebrew is required for contributor setup: https://brew.sh' >&2
  exit 2
fi
brew list node >/dev/null 2>&1 || brew install node
brew list rustup >/dev/null 2>&1 || brew install rustup
rustup toolchain install stable --profile minimal --component clippy,rustfmt
rustup default stable
npm --prefix "$app_dir" ci
"$app_dir/scripts/preflight-macos.sh"
echo 'XNOBrain app development dependencies are ready.'
