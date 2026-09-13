#!/usr/bin/env python3
"""Validate that an evaluation fixture has held-out and negative cases."""

import json
import sys

cases = json.load(sys.stdin)
if not isinstance(cases, list) or not any(
    item.get("partition") == "held_out" for item in cases if isinstance(item, dict)
):
    raise SystemExit("held-out case required")
if not any(not item.get("expected_trigger") for item in cases if isinstance(item, dict)):
    raise SystemExit("negative-trigger case required")
print(json.dumps(cases, indent=2))
