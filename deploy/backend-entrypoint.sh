#!/usr/bin/env sh
# <Summary>
# Initializes named-volume ownership, then drops privileges for Studio or the
# enterprise gateway binary selected by the Compose command.
# </Summary>
set -eu

mkdir -p /opt/data /opt/open-lumora/data
chown -R lumora:lumora /opt/data /opt/open-lumora/data
exec runuser -u lumora -- "$@"
