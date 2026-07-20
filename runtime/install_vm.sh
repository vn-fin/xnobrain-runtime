#!/usr/bin/env bash
set -euo pipefail

stage="${1:?staging directory is required}"
export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get install -y --no-install-recommends build-essential ca-certificates curl ffmpeg git python3 python3-pip python3-venv ripgrep xz-utils

mkdir -p /opt/open-lumora/extensions /opt/data/open-lumora/root /opt/data/open-lumora/profiles /opt/data/.9router
HERMES_HOME=/opt/data/open-lumora/root HERMES_INSTALL_DIR=/usr/local/lib/hermes-agent \
  bash -c 'curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash -s -- --skip-setup --non-interactive'

managed_node=/opt/data/open-lumora/root/node/bin
for command in node npm npx; do
  test -e "$managed_node/$command"
  ln -sfn "$managed_node/$command" "/usr/local/bin/$command"
done
router_source="$(mktemp -d)"
git clone --branch v0.5.40 --depth 1 https://github.com/decolua/9router.git "$router_source"
(
  cd "$router_source"
  "$managed_node/npm" install --save-exact next@16.2.10
  NEXT_TELEMETRY_DISABLED=1 "$managed_node/npm" run build
)
rm -rf /opt/open-lumora/9router
mkdir -p /opt/open-lumora/9router/.next /opt/open-lumora/9router/node_modules /opt/open-lumora/9router/src
cp -R "$router_source/public" /opt/open-lumora/9router/public
cp -R "$router_source/.next/static" /opt/open-lumora/9router/.next/static
cp -R "$router_source/.next/standalone/." /opt/open-lumora/9router/
cp -R "$router_source/open-sse" /opt/open-lumora/9router/open-sse
cp -R "$router_source/src/mitm" /opt/open-lumora/9router/src/mitm
cp -R "$router_source/node_modules/node-forge" /opt/open-lumora/9router/node_modules/node-forge
cp -R "$router_source/node_modules/next" /opt/open-lumora/9router/node_modules/next
rm -rf "$router_source"
install -m 0755 "$stage/prepare-nine-router-auth.sh" /usr/local/bin/open-lumora-prepare-nine-router-auth

hermes_python=/usr/local/lib/hermes-agent/venv/bin/python
hermes_uv=/opt/data/open-lumora/root/bin/uv
if [ ! -x "$hermes_python" ]; then
  echo "Hermes Python runtime was not installed at $hermes_python" >&2
  exit 1
fi
if [ ! -x "$hermes_uv" ]; then
  echo "Hermes uv runtime was not installed at $hermes_uv" >&2
  exit 1
fi
"$hermes_uv" pip install --python "$hermes_python" --no-cache-dir -r "$stage/extensions/requirements.txt"
cp -R "$stage/extensions/." /opt/open-lumora/extensions/
cp "$stage/hermes-custom-gateway" /usr/local/bin/hermes-custom-gateway
chmod 0755 /usr/local/bin/hermes-custom-gateway

cat >/opt/data/open-lumora/root/.env <<'EOF'
API_SERVER_ENABLED=true
API_SERVER_HOST=0.0.0.0
API_SERVER_PORT=8642
API_SERVER_KEY=local-runtime-token
EOF
chmod 600 /opt/data/open-lumora/root/.env

cat >/etc/systemd/system/open-lumora-9router.service <<'EOF'
[Unit]
Description=Open Lumora 9router runtime
After=network-online.target

[Service]
Environment=DATA_DIR=/opt/data/.9router
Environment=PORT=20128
Environment=HOSTNAME=0.0.0.0
Environment=BASE_URL=http://127.0.0.1:20128
Environment=NEXT_PUBLIC_BASE_URL=http://127.0.0.1:20128
Environment=REQUIRE_API_KEY=false
Environment=NODE_ENV=production
Environment=NINE_ROUTER_DATA_DIR=/opt/data/.9router
Environment=NINE_ROUTER_INTERNAL_SECRET=open-lumora-local-9router
ExecStartPre=/usr/local/bin/open-lumora-prepare-nine-router-auth
ExecStart=/usr/local/bin/node /opt/open-lumora/9router/server.js
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

cat >/etc/systemd/system/open-lumora-hermes.service <<EOF
[Unit]
Description=Open Lumora Hermes API runtime
After=network-online.target open-lumora-9router.service

[Service]
Environment=HERMES_HOME=/opt/data/open-lumora/root
Environment=HERMES_ROOT_PROFILE=/opt/data/open-lumora/root
Environment=HERMES_PROFILES_ROOT=/opt/data/open-lumora/profiles
Environment=HERMES_EXTENSION_ENTRYPOINT=/opt/open-lumora/extensions/hermes_custom_gateway.py
Environment=HERMES_PYTHON=$hermes_python
Environment=NINE_ROUTER_DATA_DIR=/opt/data/.9router
ExecStart=/usr/local/bin/hermes-custom-gateway
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable open-lumora-9router.service open-lumora-hermes.service
rm -rf "$stage" /root/.cache /var/lib/apt/lists/*
truncate -s 0 /etc/machine-id || true
rm -f /var/lib/dbus/machine-id
