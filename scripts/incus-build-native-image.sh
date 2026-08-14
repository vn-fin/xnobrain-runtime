#!/usr/bin/env bash
set -euo pipefail

builder_name_raw="${XNOBRAIN_INCUS_BUILDER:-xnobrain-runtime-builder}"
image_alias="${XNOBRAIN_INCUS_IMAGE_ALIAS:?XNOBRAIN_INCUS_IMAGE_ALIAS is required}"
incus_project="${XNOBRAIN_INCUS_PROJECT:-default}"
build_id="${XNOBRAIN_INCUS_BUILD_ID:-manual}"
base_image="${XNOBRAIN_INCUS_BASE_IMAGE:-images:ubuntu/24.04/cloud}"
target_member="${XNOBRAIN_INCUS_TARGET:-}"
root_disk_size="${XNOBRAIN_INCUS_IMAGE_DISK:-40GiB}"
cpu_count="${XNOBRAIN_INCUS_IMAGE_CPU:-4}"
memory_size="${XNOBRAIN_INCUS_IMAGE_MEMORY:-8GiB}"
source_archive="${XNOBRAIN_SOURCE_ARCHIVE:?XNOBRAIN_SOURCE_ARCHIVE is required}"
source_revision="${XNOBRAIN_SOURCE_REVISION:?XNOBRAIN_SOURCE_REVISION is required}"
guest_installer="/opt/xnobrain-builder/install-native-runtime.sh"

builder_name="$(
  printf '%s' "$builder_name_raw" |
    tr '[:upper:]' '[:lower:]' |
    sed -E 's/[^a-z0-9]+/-/g; s/^-+//; s/-+$//' |
    cut -c1-63 |
    sed 's/-$//'
)"
build_slug="$(printf '%s' "$build_id" | tr -cs '[:alnum:]' '-' | tr '[:upper:]' '[:lower:]' | sed 's/^-//; s/-$//' | cut -c1-20)"
candidate_alias="${image_alias:0:38}-candidate-${build_slug:-manual}"

incus_call() {
  command incus --project "$incus_project" "$@"
}

alias_fingerprint() {
  incus_call image alias list --format csv "$1" |
    awk -F, -v alias="$1" '$1 == alias { print $2; exit }'
}

hold_after_build() {
  if [[ "${XNOBRAIN_INCUS_HOLD_AFTER_BUILD:-0}" != "1" ]]; then
    return
  fi
  echo "VM image builder is idle until the next release."
  trap 'exit 0' TERM INT
  while sleep 3600; do :; done
}

if ! command -v incus >/dev/null 2>&1; then
  echo "required command not found: incus" >&2
  exit 1
fi
if [[ -z "$builder_name" ]]; then
  echo "XNOBRAIN_INCUS_BUILDER must contain at least one letter or number." >&2
  exit 1
fi
if [[ ! -s "$source_archive" || ! -x "$guest_installer" ]]; then
  echo "Runtime source archive or guest installer is unavailable." >&2
  exit 1
fi
if [[ ! "$incus_project" =~ ^[[:alnum:]_.-]+$ || ! "$image_alias" =~ ^[[:alnum:]_.-]+$ ]]; then
  echo "Incus project and image alias contain unsupported characters." >&2
  exit 1
fi

if incus_call image info "$image_alias" >/dev/null 2>&1; then
  current_revision="$(incus_call image get-property "$image_alias" xnobrain.runtime_version 2>/dev/null || true)"
  if [[ "$current_revision" == "$source_revision" ]]; then
    echo "VM image $image_alias already exists for runtime $source_revision; skipping rebuild."
    hold_after_build
    exit 0
  fi
  echo "Refusing to replace immutable VM image alias $image_alias (stored version: ${current_revision:-unknown})." >&2
  exit 1
fi

if incus_call info "$builder_name" >/dev/null 2>&1; then
  echo "Removing stale VM image builder $builder_name..."
  incus_call delete "$builder_name" --force
fi

if incus_call image info "$candidate_alias" >/dev/null 2>&1; then
  candidate_revision="$(incus_call image get-property "$candidate_alias" xnobrain.runtime_version 2>/dev/null || true)"
  candidate_fingerprint="$(alias_fingerprint "$candidate_alias")"
  if [[ "$candidate_revision" == "$source_revision" && -n "$candidate_fingerprint" ]]; then
    incus_call image alias create "$image_alias" "$candidate_fingerprint"
    incus_call image alias delete "$candidate_alias"
    echo "Recovered versioned VM image $image_alias."
    hold_after_build
    exit 0
  fi
  incus_call image alias delete "$candidate_alias"
fi

target_args=()
if [[ -n "$target_member" ]]; then
  target_args=(--target "$target_member")
fi

echo "Creating VM image builder $builder_name for $image_alias..."
incus_call init "$base_image" "$builder_name" --vm "${target_args[@]}" \
  -c "limits.cpu=$cpu_count" \
  -c "limits.memory=$memory_size" \
  -d "root,size=$root_disk_size"

cleanup_builder() {
  if incus_call info "$builder_name" >/dev/null 2>&1; then
    incus_call delete "$builder_name" --force >/dev/null 2>&1 || true
  fi
}
trap cleanup_builder EXIT

incus_call start "$builder_name"
echo "Waiting for the Incus VM agent..."
agent_ready=false
for _ in $(seq 1 180); do
  if incus_call exec "$builder_name" -- true >/dev/null 2>&1; then
    agent_ready=true
    break
  fi
  sleep 1
done
if [[ "$agent_ready" != true ]]; then
  echo "Incus VM agent did not become ready for $builder_name." >&2
  exit 1
fi

incus_call exec "$builder_name" -- cloud-init status --wait
if ! incus_call exec "$builder_name" -- curl --fail --silent --show-error \
  --connect-timeout 10 --max-time 20 -4 https://archive.ubuntu.com/ubuntu/ >/dev/null; then
  echo "The Incus VM has no outbound IPv4 access." >&2
  exit 1
fi

incus_call file push "$guest_installer" "$builder_name/tmp/install-native-runtime.sh"
incus_call file push "$source_archive" "$builder_name/tmp/xnobrain-source.tar"
incus_call exec "$builder_name" -- chmod 0700 /tmp/install-native-runtime.sh
incus_call exec "$builder_name" \
  --env "XNOBRAIN_SOURCE_ARCHIVE=/tmp/xnobrain-source.tar" \
  --env "XNOBRAIN_SOURCE_REVISION=$source_revision" \
  -- /tmp/install-native-runtime.sh

echo "Preparing reusable VM image..."
incus_call exec "$builder_name" -- systemctl stop xnobrain.target
primary_interface="$(incus_call exec "$builder_name" -- sh -c "ip -4 route show default | awk 'NR == 1 { print \$5 }'")"
if [[ ! "$primary_interface" =~ ^[[:alnum:]_.:-]+$ ]]; then
  echo "Could not determine the VM network interface." >&2
  exit 1
fi
incus_call exec "$builder_name" -- netplan set "ethernets.${primary_interface}.dhcp-identifier=mac"
incus_call exec "$builder_name" -- netplan generate
incus_call exec "$builder_name" -- rm -f /tmp/install-native-runtime.sh /tmp/xnobrain-source.tar
incus_call exec "$builder_name" -- find /srv/xnobrain-data -mindepth 1 -delete
incus_call exec "$builder_name" -- chown xnobrain:xnobrain /srv/xnobrain-data
incus_call exec "$builder_name" -- /opt/xnobrain/.tools/python/bin/python -c 'import hermes_cli'
incus_call exec "$builder_name" -- apt-get clean
incus_call exec "$builder_name" -- cloud-init clean --logs --machine-id
incus_call stop "$builder_name" --timeout 120

echo "Publishing candidate $candidate_alias..."
incus_call publish "$builder_name" \
  --alias "$candidate_alias" \
  --expire 2099-12-31T23:59:59Z \
  "xnobrain.runtime_version=$source_revision"
incus_call delete "$builder_name"
trap - EXIT

candidate_fingerprint="$(alias_fingerprint "$candidate_alias")"
if [[ -z "$candidate_fingerprint" ]]; then
  echo "Candidate VM image did not return a fingerprint." >&2
  exit 1
fi
incus_call image alias create "$image_alias" "$candidate_fingerprint"
incus_call image alias delete "$candidate_alias"

echo "Published versioned XNOBrain runtime VM image: $image_alias"
hold_after_build
