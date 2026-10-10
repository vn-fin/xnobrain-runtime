#!/usr/bin/python3
"""Root-owned socket-activated one-shot helper; no configurable command or path.

Only the fixed clean page-cache operation is privileged. Runtime's unprivileged
admission protocol owns eligibility; systemd owns deadline and cgroup lifetime.
"""

import json
import os
import pwd
import re
import socket
import struct
import sys
from pathlib import Path

RECEIPT = Path("/var/lib/xnobrain-memory-reclaim/receipt.json")


def supported():
    if os.getenv("FT_ENABLE_WORKSPACE_IDLE_MEMORY_RECLAIM", "false").lower() != "true":
        return False
    if os.sysconf("SC_PAGE_SIZE") != 4096:
        return False
    if (
        Path("/sys/module/page_reporting/parameters/page_reporting_order").read_text().strip()
        != "0"
    ):
        return False
    for device in Path("/sys/bus/virtio/devices").iterdir():
        if int((device / "device").read_text().strip(), 16) == 5:
            bits = (device / "features").read_text().strip()
            return len(bits) > 5 and bits[5] == "1"
    return False


def sample():
    result = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, value = line.split(":", 1)
        result[key] = int(value.strip().split()[0]) * 1024
    return result


def save(value):
    # StateDirectory is root-owned and not writable by the Runtime user.
    path = RECEIPT.with_suffix(".new")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o640)
    with os.fdopen(descriptor, "w") as output:
        json.dump(value, output)
        output.flush()
        os.fsync(output.fileno())
    os.replace(path, RECEIPT)
    descriptor = os.open(RECEIPT.parent, os.O_DIRECTORY | os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def execute(request):
    if (
        not isinstance(request, dict)
        or set(request) != {"operation_id", "fence"}
        or not isinstance(request["operation_id"], str)
        or not re.fullmatch(r"mem_[a-zA-Z0-9_-]{1,100}", request["operation_id"])
        or type(request["fence"]) is not int
        or not 1 <= request["fence"] < 2**63
    ):
        raise ValueError("invalid request")
    previous = json.loads(RECEIPT.read_text()) if RECEIPT.exists() else {}
    if previous.get("operation_id") == request["operation_id"]:
        if previous.get("fence") != request["fence"]:
            raise ValueError("fence mismatch")
        return previous
    if previous.get("fence", 0) >= request["fence"]:
        raise ValueError("stale fence")
    before = sample()
    value = {
        **request,
        "guest_boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        "state": "executing",
        "cached_before_bytes": before["Cached"],
    }
    save(value)
    if not supported():
        value.update(state="skipped", reason="backend_unavailable")
    else:
        # Writing 1 drops only clean page cache; dirty and writeback pages stay,
        # so guest pressure and cache size are not safety conditions here.
        # No sync, slab eviction, process killing, ballooning or quota change.
        with Path("/proc/sys/vm/drop_caches").open("w") as target:
            target.write("1\n")
        value.update(state="evicted", reason="")
    value["cached_after_bytes"] = sample()["Cached"]
    save(value)
    return value


def main():
    if len(sys.argv) != 1 or os.geteuid() != 0:
        return 64
    if os.getenv("LISTEN_PID") != str(os.getpid()) or os.getenv("LISTEN_FDS") != "1":
        return 64
    with socket.socket(fileno=3) as listener:
        listener.settimeout(2)
        connection, _ = listener.accept()
        with connection:
            connection.settimeout(1)
            _, uid, _ = struct.unpack(
                "3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
            )
            if uid != pwd.getpwnam("xnobrain").pw_uid:
                return 77
            with connection.makefile("rb") as source:
                line = source.readline(513)
            if len(line) > 512 or not line.endswith(b"\n"):
                return 64
            result = execute(json.loads(line))
            connection.sendall(json.dumps(result).encode() + b"\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
