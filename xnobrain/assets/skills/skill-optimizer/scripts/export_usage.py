#!/usr/bin/env python3
"""Print a bounded skill-usage API response supplied on stdin."""

import json
import sys

value = json.load(sys.stdin)
print(json.dumps(value, indent=2, sort_keys=True))
