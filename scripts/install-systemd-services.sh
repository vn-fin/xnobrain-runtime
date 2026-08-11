#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
install_root="/opt/brain4all"
service_user="brain4all"
data_root="/srv/brain4all-data"
start_services=false

usage() {
  cat <<'EOF'
Install Brain4All's native systemd services.

The Brain4All checkout and runtime must already be installed at /opt/brain4all.

Usage: sudo ./scripts/install-systemd-services.sh [--start]

Options:
  --start     Start brain4all.target after installing and enabling the units
  -h, --help  Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --start) start_services=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this installer as root (for example, with sudo)." >&2
  exit 1
fi
if [[ "$project_dir" != "$install_root" ]]; then
  echo "Brain4All must be checked out at $install_root; found $project_dir" >&2
  exit 1
fi
for path in \
  "$install_root/.tools/python/bin/python" \
  "$install_root/.tools/node/bin/node" \
  "$install_root/.tools/npm-global/lib/node_modules/9router/app/custom-server.js" \
  "$install_root/.tools/npm-global/lib/node_modules/9router/app/node_modules/next/dist/server/dev/browser-logs/file-logger.js" \
  "$install_root/.tools/hermes-agent/venv/bin/hermes"; do
  if [[ ! -e "$path" ]]; then
    echo "Required runtime path not found: $path" >&2
    echo "Run scripts/install-linux.sh before installing the services." >&2
    exit 1
  fi
done
ln -sfn \
  "$install_root/.tools/hermes-agent/venv/bin/hermes" \
  "$install_root/.tools/npm-global/bin/agent"

if ! getent passwd "$service_user" >/dev/null; then
  useradd \
    --system \
    --home-dir "$data_root/home" \
    --create-home \
    --shell /bin/bash \
    "$service_user"
fi

install -d -o "$service_user" -g "$service_user" -m 0700 \
  "$data_root" \
  "$data_root/home" \
  "$data_root/brain4all"
chown -R "$service_user:$service_user" "$install_root"

install -d -o root -g "$service_user" -m 0750 /etc/brain4all
install -m 0640 -o root -g "$service_user" \
  "$install_root/deploy/systemd/brain4all.env.example" \
  /etc/brain4all/brain4all.env.example
if [[ ! -e /etc/brain4all/brain4all.env ]]; then
  install -m 0640 -o root -g "$service_user" \
    "$install_root/deploy/systemd/brain4all.env.example" \
    /etc/brain4all/brain4all.env
fi

for unit in \
  brain4all-prepare.service \
  brain4all-9router.service \
  brain4all-api.service \
  brain4all.target; do
  install -m 0644 \
    "$install_root/deploy/systemd/$unit" \
    "/etc/systemd/system/$unit"
done
chmod 0755 "$install_root/scripts/prepare-service-data.sh"

systemctl daemon-reload
systemctl enable brain4all.target
if [[ "$start_services" == true ]]; then
  for variable_name in HERMES_HOME NINE_ROUTER_DATA_DIR; do
    if ! grep -Eq "^${variable_name}=[^[:space:]].*" /etc/brain4all/brain4all.env; then
      echo "$variable_name is required in /etc/brain4all/brain4all.env before services can start." >&2
      exit 1
    fi
  done
  systemctl restart brain4all.target
fi

echo "Brain4All systemd services installed."
echo "Configuration: /etc/brain4all/brain4all.env"
echo "Persistent data: $data_root"
echo "API: http://<private-vm-ip>:8642"
