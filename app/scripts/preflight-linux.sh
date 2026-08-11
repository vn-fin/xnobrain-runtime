#!/usr/bin/env bash
set -euo pipefail

[[ "$(uname -s)" == Linux ]] || { echo 'Linux packaging must run on Linux.' >&2; exit 2; }
for command in node npm rustc cargo pkg-config; do
  command -v "$command" >/dev/null 2>&1 || { echo "$command is missing. Run make first." >&2; exit 2; }
done
pkg-config --exists webkit2gtk-4.1 || { echo 'webkit2gtk-4.1 development files are missing. Run make first.' >&2; exit 2; }
pkg-config --exists gtk+-3.0 || { echo 'GTK 3 development files are missing. Run make first.' >&2; exit 2; }

case "$(rustc -vV | awk '/^host:/ { print $2 }')" in
  x86_64-unknown-linux-gnu|aarch64-unknown-linux-gnu) ;;
  *) echo 'The active Rust toolchain is not a supported native Linux GNU target.' >&2; exit 2 ;;
esac

echo 'Linux native build preflight passed.'
