#!/usr/bin/env python3
"""Scaffold a new Hermes built-in tool module from the skill's template.

Generates ``.tools/hermes-agent/tools/<name>_tool.py`` (self-registering).

Usage:
    python3 new_tool.py <tool_name> [--toolset custom] [--emoji 🔧]
                        [--description "..."] [--dest DIR] [--force] [--stdout]

Examples:
    python3 new_tool.py hello_world --toolset custom --emoji 👋
    python3 new_tool.py fetch_quote --description "Fetch a random quote."
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
TEMPLATE = SKILL_DIR / "assets" / "tool_template.py"

# Default destination: the vendored Hermes source in this repo, if present.
REPO_ROOT = Path.cwd()
DEFAULT_DEST = REPO_ROOT / ".tools" / "hermes-agent" / "tools"

VALID_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


def render(name: str, toolset: str, emoji: str, description: str) -> str:
    text = TEMPLATE.read_text(encoding="utf-8")
    return (
        text.replace("{{TOOL_NAME_UPPER}}", name.upper())
        .replace("{{TOOL_NAME}}", name)
        .replace("{{TOOLSET}}", toolset)
        .replace("{{EMOJI}}", emoji)
        .replace("{{DESCRIPTION}}", description)
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name", help="tool name (snake_case, e.g. hello_world)")
    ap.add_argument("--toolset", default="custom", help="toolset grouping (default: custom)")
    ap.add_argument("--emoji", default="🔧", help="emoji shown in UIs")
    ap.add_argument("--description", default="Describe what this tool does and when to use it.")
    ap.add_argument("--dest", type=Path, default=DEFAULT_DEST,
                    help="destination tools/ dir (default: ./.tools/hermes-agent/tools)")
    ap.add_argument("--force", action="store_true", help="overwrite if file exists")
    ap.add_argument("--stdout", action="store_true", help="print to stdout instead of writing a file")
    args = ap.parse_args()

    if not VALID_NAME.match(args.name):
        ap.error(f"invalid tool name {args.name!r}: use snake_case [a-z][a-z0-9_]*")
    if not TEMPLATE.exists():
        ap.error(f"template not found: {TEMPLATE}")

    content = render(args.name, args.toolset, args.emoji, args.description)

    if args.stdout:
        sys.stdout.write(content)
        return 0

    dest_dir = args.dest
    if not dest_dir.exists():
        print(f"! destination {dest_dir} does not exist.", file=sys.stderr)
        print("  Pass --dest <hermes>/tools, or use --stdout to preview.", file=sys.stderr)
        return 2

    out = dest_dir / f"{args.name}_tool.py"
    if out.exists() and not args.force:
        ap.error(f"{out} already exists (use --force to overwrite)")
    out.write_text(content, encoding="utf-8")
    print(f"✓ wrote {out}")
    print(f"  next: python3 {SKILL_DIR/'scripts'/'verify_extension.py'} --tool {args.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
