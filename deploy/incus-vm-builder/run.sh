#!/usr/bin/env bash
set -euo pipefail

runtime_version="${RUNTIME_VERSION:?RUNTIME_VERSION is required}"
if ! [[ "$runtime_version" =~ ^[0-9]+\.[0-9]+\.[0-9]+([.-][0-9A-Za-z.-]+)?$ ]]; then
  echo "Invalid RUNTIME_VERSION: $runtime_version" >&2
  exit 1
fi

source_archive="$(mktemp /tmp/brain4all-source.XXXXXX.tar)"
cleanup() {
  rm -f -- "$source_archive"
}
trap cleanup EXIT

tar -C /opt/brain4all-source -cf "$source_archive" .
export BRAIN4ALL_SOURCE_ARCHIVE="$source_archive"
export BRAIN4ALL_SOURCE_REVISION="$runtime_version"

/opt/brain4all-builder/incus-build-native-image.sh
