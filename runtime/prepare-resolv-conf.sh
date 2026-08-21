#!/usr/bin/env bash
set -euo pipefail

# Incus creates an empty regular /etc/resolv.conf while instantiating an OCI
# image. Replace it at boot, after the root filesystem is writable and before
# systemd-resolved or the runtime can make outbound requests.
ln -sfn /run/systemd/resolve/stub-resolv.conf /etc/resolv.conf
