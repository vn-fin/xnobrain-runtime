#!/usr/bin/env bash
# Run only against a clean image or an offline, trusted managed container.
set -euo pipefail

[[ "$(id -u)" == 0 ]] || { echo "Container hardening requires root" >&2; exit 1; }
[[ -d /opt/xnobrain && ! -L /opt/xnobrain ]] || { echo "Invalid Runtime root" >&2; exit 1; }
[[ "$(id -u xnobrain)" != 0 && "$(id -g xnobrain)" != 0 ]] || exit 1
state="$(systemctl show xnobrain-runtime.service --property=ActiveState --value)"
case "$state" in inactive|failed) ;; *) echo "Stop Runtime before hardening" >&2; exit 1 ;; esac
[[ "$(systemctl show xnobrain-runtime.service --property=MainPID --value)" == 0 ]] || exit 1
[[ "$(systemctl show xnobrain-runtime.service --property=ControlPID --value)" == 0 ]] || exit 1

# Account groups must not grant a second route to root. Native image build has
# already removed its temporary sudo policy before this helper is called.
usermod -G '' xnobrain
usermod -L xnobrain
for parent in /opt /usr/local /usr/local/bin /usr/local/lib /usr/local/libexec /etc/xnobrain; do
  [[ ! -L "$parent" ]] || { echo "Invalid protected directory" >&2; exit 1; }
  install -d -o root -g root -m 0755 "$parent"
done

# Do not follow dependency/profile symlinks. Physically contained dependencies
# are protected along with source; user-owned data is never chmodded here.
chown -hR root:root /opt/xnobrain
find /opt/xnobrain -xdev -type d -exec chmod a+rx,go-w {} +
find /opt/xnobrain -xdev -type f -exec chmod a+r,go-w,u-s,g-s {} +

install -o root -g root -m 0755 /opt/xnobrain/runtime/container-entrypoint.sh /usr/local/bin/xnobrain-runtime
install -o root -g root -m 0755 /opt/xnobrain/scripts/apply-profile-templates.sh /usr/local/bin/xnobrain-apply-profile-templates
install -o root -g root -m 0755 /opt/xnobrain/runtime/prepare-container.py /usr/local/libexec/xnobrain-prepare-container.py
install -o root -g root -m 0755 /opt/xnobrain/runtime/systemd-entrypoint.sh /usr/local/bin/xnobrain-systemd-entrypoint
install -o root -g root -m 0644 /opt/xnobrain/runtime/xnobrain-runtime-prepare.service /etc/systemd/system/xnobrain-runtime-prepare.service
install -o root -g root -m 0644 /opt/xnobrain/runtime/xnobrain-runtime.service /etc/systemd/system/xnobrain-runtime.service
# Incus/LXC supplies a late global service drop-in which disables NNP. Restore
# it for this workload only; setpriv in ExecStart also enforces it at exec time.
install -d -o root -g root -m 0755 /etc/systemd/system/xnobrain-runtime.service.d
printf '[Service]\nNoNewPrivileges=yes\n' > /etc/systemd/system/xnobrain-runtime.service.d/zzzz-xnobrain-hardening.conf
chown root:root /etc/systemd/system/xnobrain-runtime.service.d/zzzz-xnobrain-hardening.conf
chmod 0644 /etc/systemd/system/xnobrain-runtime.service.d/zzzz-xnobrain-hardening.conf
install -o root -g root -m 0644 /dev/null /etc/xnobrain/container-hardening-v1
systemctl daemon-reload
