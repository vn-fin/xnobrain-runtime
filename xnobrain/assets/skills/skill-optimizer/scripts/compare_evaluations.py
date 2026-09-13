#!/usr/bin/env python3
"""Summarize baseline and candidate pass totals from a safe evaluation report."""

import json
import sys

report = json.load(sys.stdin)
for patch in report.get("patches", []):
    print(
        patch.get("skill_id"),
        patch.get("baseline_passed", 0),
        patch.get("candidate_passed", 0),
    )
