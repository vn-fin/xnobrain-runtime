#!/usr/bin/env bash
set -euo pipefail

install_root="/opt/xnobrain"
data_root="/srv/xnobrain-data"
service_user="xnobrain"
source_archive="${XNOBRAIN_SOURCE_ARCHIVE:?XNOBRAIN_SOURCE_ARCHIVE is required}"
source_revision="${XNOBRAIN_SOURCE_REVISION:?XNOBRAIN_SOURCE_REVISION is required}"

if [[ "$EUID" -ne 0 ]]; then
  echo "The native XNOBrain guest installer must run as root." >&2
  exit 1
fi
if [[ ! -s "$source_archive" ]]; then
  echo "XNOBrain source archive is missing or empty." >&2
  exit 1
fi
if [[ -e "$install_root" ]]; then
  echo "Installation path already exists: $install_root" >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends ca-certificates curl sudo

if ! getent passwd "$service_user" >/dev/null; then
  useradd \
    --system \
    --home-dir "$data_root/home" \
    --create-home \
    --shell /bin/bash \
    "$service_user"
fi
install -d -o "$service_user" -g "$service_user" -m 0700 "$data_root" "$data_root/home"
install -d -o "$service_user" -g "$service_user" -m 0755 "$install_root"

echo "Installing XNOBrain runtime $source_revision from the release source archive..."
tar -xf "$source_archive" -C "$install_root"
chown -R "$service_user:$service_user" "$install_root"
printf '%s\n' "$source_revision" >/etc/xnobrain-revision
chmod 0444 /etc/xnobrain-revision

sudoers_file="/etc/sudoers.d/xnobrain-native-installer"
cleanup() {
  rm -f -- "$sudoers_file"
}
trap cleanup EXIT
printf '%s\n' "$service_user ALL=(root) NOPASSWD: ALL" >"$sudoers_file"
chmod 0440 "$sudoers_file"
visudo --check --file "$sudoers_file" >/dev/null

echo "Installing the native XNOBrain backend..."
runuser -u "$service_user" -- env \
  HOME="$data_root/home" \
  HERMES_HOME="$data_root/hermes/root" \
  NINE_ROUTER_DATA_DIR="$data_root/9router" \
  UV_PYTHON_INSTALL_DIR="$install_root/.tools/uv-python" \
  bash -c 'cd "$1" && exec bash "$1/scripts/install-linux.sh" --skip-browser' \
  xnobrain-installer "$install_root"

# Keep the Python interpreter inside the immutable application tree. The
# service user's data home is cleared before publishing the reusable image.
project_venv="$install_root/.tools/hermes-agent/venv"
python_link="$project_venv/bin/python"
python_runtime="$(readlink -f "$python_link")"
case "$python_runtime" in
  "$data_root"/home/.local/share/uv/python/*/bin/python*)
    python_runtime_dir="$(dirname "$(dirname "$python_runtime")")"
    python_runtime_name="$(basename "$python_runtime_dir")"
    durable_python_root="$install_root/.tools/uv-python"
    durable_python_dir="$durable_python_root/$python_runtime_name"
    install -d -o "$service_user" -g "$service_user" -m 0755 "$durable_python_root"
    cp -a "$python_runtime_dir" "$durable_python_dir"
    chown -R "$service_user:$service_user" "$durable_python_dir"
    ln -sfn "$durable_python_dir/bin/$(basename "$python_runtime")" "$python_link"
    sed -i "s|^home = .*|home = $durable_python_dir/bin|" "$project_venv/pyvenv.cfg"
    ;;
esac

if [[ ! -x "$python_link" ]] || ! "$python_link" -c 'import hermes_cli' >/dev/null 2>&1; then
  echo "XNOBrain Python runtime is not self-contained after installation." >&2
  exit 1
fi

rm -f -- "$sudoers_file"
trap - EXIT

"$install_root/scripts/install-systemd-services.sh"
sed -i \
  -e "s|^HERMES_HOME=.*|HERMES_HOME=$data_root/hermes/root|" \
  -e "s|^NINE_ROUTER_DATA_DIR=.*|NINE_ROUTER_DATA_DIR=$data_root/9router|" \
  -e "s|^DATA_DIR=.*|DATA_DIR=$data_root/xnobrain|" \
  /etc/xnobrain/xnobrain.env
if grep -q '^HERMES_PROFILES_ROOT=' /etc/xnobrain/xnobrain.env; then
  sed -i \
    "s|^HERMES_PROFILES_ROOT=.*|HERMES_PROFILES_ROOT=$data_root/hermes/profiles|" \
    /etc/xnobrain/xnobrain.env
else
  printf '%s\n' "HERMES_PROFILES_ROOT=$data_root/hermes/profiles" \
    >>/etc/xnobrain/xnobrain.env
fi
systemctl restart xnobrain.target

for _ in $(seq 1 60); do
  if curl --fail --silent --show-error \
    http://127.0.0.1:8642/xnobrain/api/runtime/v1/health >/dev/null; then
    echo "XNOBrain native runtime is healthy on port 8642."
    exit 0
  fi
  sleep 2
done

echo "XNOBrain API did not become healthy." >&2
systemctl --no-pager --full status xnobrain-api.service >&2 || true
journalctl --no-pager -u xnobrain-api.service -n 100 >&2 || true
exit 1
