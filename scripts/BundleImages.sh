#!/usr/bin/env bash
set -euo pipefail

container_cli="${CONTAINER_CLI:-docker}"
image_tag="${IMAGE_TAG:-local}"
output_dir="${BUNDLE_OUTPUT_DIR:-bin/images}"
part_size="${IMAGE_BUNDLE_PART_SIZE:-47m}"
bundle="open-lumora-images-${image_tag}"
images=(
  "${OPEN_LUMORA_BACKEND_IMAGE:-open-lumora-backend:${image_tag}}"
  "${OPEN_LUMORA_FRONTEND_IMAGE:-open-lumora-frontend:${image_tag}}"
  "${HERMES_RUNTIME_IMAGE:-open-lumora-hermes-runtime:${image_tag}}"
)

command -v "$container_cli" >/dev/null 2>&1 || { echo "Container CLI '$container_cli' is not installed." >&2; exit 2; }
command -v gzip >/dev/null 2>&1 || { echo "gzip is required." >&2; exit 2; }
command -v split >/dev/null 2>&1 || { echo "split is required." >&2; exit 2; }

for image in "${images[@]}"; do
  "$container_cli" image inspect "$image" >/dev/null
done

mkdir -p "$output_dir"
find "$output_dir" -maxdepth 1 -type f -name "${bundle}.tar.gz.part-*" -delete
rm -f "$output_dir/${bundle}.sha256" "$output_dir/${bundle}.manifest"

"$container_cli" save "${images[@]}" | gzip -9 | split -b "$part_size" -d -a 4 - "$output_dir/${bundle}.tar.gz.part-"
(
  cd "$output_dir"
  sha256sum "${bundle}.tar.gz.part-"* >"${bundle}.sha256"
)
{
  printf 'format=open-lumora-image-bundle-v1\n'
  printf 'tag=%s\n' "$image_tag"
  printf 'part_size=%s\n' "$part_size"
  printf 'image=%s\n' "${images[@]}"
} >"$output_dir/${bundle}.manifest"

find "$output_dir" -maxdepth 1 -type f -name "${bundle}.tar.gz.part-*" -size +49999999c -print -quit | grep -q . \
  && { echo "An image bundle part exceeded the 50 MB repository limit." >&2; exit 1; } || true
echo "Image bundle written to $output_dir in parts smaller than 50 MB."
