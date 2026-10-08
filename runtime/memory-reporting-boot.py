#!/usr/bin/python3
"""Enable the qualified reporting order during explicit guest service lifecycle."""

import os
from pathlib import Path


def main():
    if os.getenv("FT_ENABLE_WORKSPACE_IDLE_MEMORY_RECLAIM", "false").lower() != "true":
        return
    if os.geteuid() != 0 or os.uname().release != "6.8.0-146-generic":
        return
    if os.sysconf("SC_PAGE_SIZE") != 4096:
        return
    for device in Path("/sys/bus/virtio/devices").iterdir():
        if int((device / "device").read_text().strip(), 16) != 5:
            continue
        bits = (device / "features").read_text().strip()
        if len(bits) > 5 and bits[5] == "1":
            Path("/sys/module/page_reporting/parameters/page_reporting_order").write_text("0\n")
        return


if __name__ == "__main__":
    main()
