#!/usr/bin/env bash
set -euo pipefail

runtime_version="${RUNTIME_VERSION:?RUNTIME_VERSION is required}"
if ! [[ "$runtime_version" =~ ^[0-9]+\.[0-9]+\.[0-9]+([.-][0-9A-Za-z.-]+)?$ ]]; then
  echo "Invalid RUNTIME_VERSION: $runtime_version" >&2
  exit 1
fi

source_archive="$(mktemp /tmp/xnobrain-source.XXXXXX.tar)"
cleanup() {
  rm -f -- "$source_archive"
}
trap cleanup EXIT

tar -C /opt/xnobrain-source -cf "$source_archive" .
export XNOBRAIN_SOURCE_ARCHIVE="$source_archive"
export XNOBRAIN_SOURCE_REVISION="$runtime_version"

/opt/xnobrain-builder/incus-build-native-image.sh
