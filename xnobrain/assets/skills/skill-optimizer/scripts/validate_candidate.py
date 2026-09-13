#!/usr/bin/env python3
"""Perform a minimal local SKILL.md frontmatter check."""

import pathlib
import sys

text = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
if not text.startswith("---") or "\nname:" not in text or "\ndescription:" not in text:
    raise SystemExit("candidate requires name and description frontmatter")
