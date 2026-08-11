#!/usr/bin/env bash
set -euo pipefail

app_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if command -v apt-get >/dev/null 2>&1; then
  packages=(build-essential curl file libayatana-appindicator3-dev libgtk-3-dev librsvg2-dev libssl-dev libwebkit2gtk-4.1-dev libxdo-dev npm pkg-config rpm wget)
  missing=()
  for package in "${packages[@]}"; do
    dpkg-query -W -f='${Status}' "$package" 2>/dev/null | grep -q 'install ok installed' || missing+=("$package")
  done
  if ((${#missing[@]})); then
    sudo apt-get update
    sudo apt-get install -y "${missing[@]}"
  fi
elif command -v dnf >/dev/null 2>&1; then
  sudo dnf install -y '@development-tools' cargo curl file javascriptcoregtk4.1-devel libappindicator-gtk3-devel librsvg2-devel nodejs npm openssl-devel rpm-build webkit2gtk4.1-devel wget
else
  echo 'Supported development setup requires apt-get or dnf. See Tauri prerequisites for this distribution.' >&2
  exit 2
fi

if ! command -v rustup >/dev/null 2>&1; then
  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal
  export PATH="${CARGO_HOME:-$HOME/.cargo}/bin:$PATH"
fi
rustup toolchain install stable --profile minimal --component clippy,rustfmt
rustup default stable
npm --prefix "$app_dir" ci
"$app_dir/scripts/preflight-linux.sh"
echo 'XNOBrain app development dependencies are ready.'
