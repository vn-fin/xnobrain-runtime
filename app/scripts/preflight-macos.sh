#!/usr/bin/env bash
set -euo pipefail

[[ "$(uname -s)" == Darwin ]] || { echo 'macOS packaging must run on macOS.' >&2; exit 2; }
xcode-select -p >/dev/null 2>&1 || { echo 'Xcode Command Line Tools are missing. Run make first.' >&2; exit 2; }
for command in clang hdiutil codesign node npm rustc cargo; do
  command -v "$command" >/dev/null 2>&1 || { echo "$command is missing. Run make first." >&2; exit 2; }
done

host_triple="$(rustc -vV | awk '/^host:/ { print $2 }')"
case "$(uname -m):$host_triple" in
  arm64:aarch64-apple-darwin|x86_64:x86_64-apple-darwin) ;;
  *) echo "Rust host $host_triple does not match this macOS machine." >&2; exit 2 ;;
esac

if [[ "${XNOBRAIN_REQUIRE_SIGNING:-0}" == 1 ]]; then
  [[ -n "${APPLE_SIGNING_IDENTITY:-}" ]] || { echo 'APPLE_SIGNING_IDENTITY is required for a signed release.' >&2; exit 2; }
  security find-identity -v -p codesigning | grep -F -- "$APPLE_SIGNING_IDENTITY" >/dev/null || {
    echo 'APPLE_SIGNING_IDENTITY is not available in the current keychain.' >&2
    exit 2
  }
  api_credentials=false
  apple_id_credentials=false
  [[ -n "${APPLE_API_ISSUER:-}" && -n "${APPLE_API_KEY:-}" && -f "${APPLE_API_KEY_PATH:-}" ]] && api_credentials=true
  [[ -n "${APPLE_ID:-}" && -n "${APPLE_PASSWORD:-}" && -n "${APPLE_TEAM_ID:-}" ]] && apple_id_credentials=true
  [[ "$api_credentials" == true || "$apple_id_credentials" == true ]] || {
    echo 'Apple notarization credentials are required for a signed release.' >&2
    exit 2
  }
fi

echo 'macOS native build preflight passed.'
