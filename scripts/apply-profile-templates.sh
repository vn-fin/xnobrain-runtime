#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_dir="$(cd -- "$script_dir/.." && pwd)"
templates_dir="${BRAIN4ALL_PROFILE_TEMPLATES_DIR:-$project_dir/runtime/profile-templates}"
hermes_home="${1:-${HERMES_HOME:-$HOME/.hermes}}"
backup_stamp="$(date -u +%Y%m%dT%H%M%SZ)"
profile_template="$hermes_home/profile-template"

if [[ ! -f "$templates_dir/config.yaml" || ! -f "$templates_dir/AGENTS.md" || ! -f "$templates_dir/SOUL.md" ]]; then
  echo "Brain4All profile templates are missing from $templates_dir" >&2
  exit 1
fi

apply_file() {
  local profile_dir="$1"
  local source="$2"
  local target="$3"
  local relative_target="$4"

  mkdir -p "$(dirname "$target")"
  if [[ -f "$target" ]] && cmp -s "$source" "$target"; then
    return
  fi
  if [[ -f "$target" ]]; then
    local backup="$profile_dir/snapshots/profile-templates/$backup_stamp/$relative_target"
    mkdir -p "$(dirname "$backup")"
    cp -p "$target" "$backup"
  fi
  install -m 0644 "$source" "$target"
}

mkdir -p "$hermes_home" "$profile_template" "$hermes_home/workspace"
for filename in config.yaml SOUL.md AGENTS.md; do
  apply_file \
    "$hermes_home" \
    "$templates_dir/$filename" \
    "$profile_template/$filename" \
    "profile-template/$filename"
done

# Big Brother starts from the packaged guidance, while the independent
# profile-template remains unchanged when Big Brother later edits its files.
apply_file "$hermes_home" "$templates_dir/SOUL.md" "$hermes_home/SOUL.md" "SOUL.md"
apply_file "$hermes_home" "$templates_dir/AGENTS.md" "$hermes_home/AGENTS.md" "AGENTS.md"
apply_file "$hermes_home" "$templates_dir/AGENTS.md" "$hermes_home/workspace/AGENTS.md" "workspace/AGENTS.md"

echo "Installed the independent Brain4All profile template and refreshed Big Brother guidance."
