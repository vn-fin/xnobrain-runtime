#!/usr/bin/env sh
# <Summary>
# Initializes the public profile-volume ownership, then starts Studio without
# carrying or selecting any enterprise executable.
# </Summary>
set -eu

mkdir -p /opt/data
chown -R lumora:lumora /opt/data
exec runuser -u lumora -- "$@"
