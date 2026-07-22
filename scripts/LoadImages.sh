#!/usr/bin/env bash
set -euo pipefail

container_cli="${CONTAINER_CLI:-docker}"
input_dir="${BUNDLE_INPUT_DIR:-bin/images}"
manifest="$(find "$input_dir" -maxdepth 1 -type f -name 'brain4all-images-*.manifest' -print -quit)"
if [ -z "$manifest" ]; then
  echo "No image bundle manifest found under $input_dir." >&2
  exit 2
fi
bundle="$(basename "$manifest" .manifest)"

command -v "$container_cli" >/dev/null 2>&1 || { echo "Container CLI '$container_cli' is not installed." >&2; exit 2; }
(
  cd "$input_dir"
  sha256sum -c "${bundle}.sha256"
  cat "${bundle}.tar.gz.part-"* | gzip -dc | "$container_cli" load
)
