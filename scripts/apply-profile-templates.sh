#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_dir="$(cd -- "$script_dir/.." && pwd)"
templates_dir="${BRAIN4ALL_PROFILE_TEMPLATES_DIR:-$project_dir/runtime/profile-templates}"
hermes_home="${1:-${HERMES_HOME:-$HOME/.hermes}}"
profiles_root="${2:-${HERMES_PROFILES_ROOT:-$hermes_home/profiles}}"
backup_stamp="$(date -u +%Y%m%dT%H%M%SZ)"

if [[ ! -f "$templates_dir/AGENTS.md" || ! -f "$templates_dir/SOUL.md" ]]; then
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

apply_profile() {
  local profile_dir="$1"
  [[ -d "$profile_dir" ]] || return
  mkdir -p "$profile_dir/workspace"
  apply_file "$profile_dir" "$templates_dir/SOUL.md" "$profile_dir/SOUL.md" "SOUL.md"
  apply_file "$profile_dir" "$templates_dir/AGENTS.md" "$profile_dir/AGENTS.md" "AGENTS.md"
  apply_file "$profile_dir" "$templates_dir/AGENTS.md" "$profile_dir/workspace/AGENTS.md" "workspace/AGENTS.md"
}

mkdir -p "$hermes_home" "$profiles_root"
apply_profile "$hermes_home"
for profile_dir in "$profiles_root"/*; do
  [[ -d "$profile_dir" ]] || continue
  apply_profile "$profile_dir"
done

echo "Applied Brain4All workspace guidance to the default and named profiles."
