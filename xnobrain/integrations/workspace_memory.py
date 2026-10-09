"""Fixed, unprivileged client for the socket-activated guest cache helper."""

from __future__ import annotations

import asyncio
import json
import os
import stat
from pathlib import Path

SOCKET = "/run/xnobrain-memory-reclaim.sock"
RECEIPT = Path("/var/lib/xnobrain-memory-reclaim/receipt.json")
UNIT = "xnobrain-memory-reclaim.service"
BACKEND = "virtio-reporting-order0-4k-v1"
HELPER_STATUS_COMMAND = (
    "/usr/bin/systemctl",
    "show",
    UNIT,
    "--property=LoadState,ActiveState,ControlGroup,Job",
)


def _passive_helper_probe(entry: Path, owner: int) -> bool:
    """Only our exact read-only supervisor probe is housekeeping, not user work."""
    raw = (entry / "stat").read_text()
    parent = int(raw[raw.rfind(") ") + 2 :].split()[1])
    return (
        parent == owner
        and os.readlink(entry / "exe") == HELPER_STATUS_COMMAND[0]
        and (entry / "cmdline").read_bytes()
        == b"\0".join(arg.encode() for arg in HELPER_STATUS_COMMAND) + b"\0"
    )


def memory_sample() -> dict[str, int]:
    values = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        name, value = line.split(":", 1)
        values[name] = int(value.strip().split()[0]) * 1024
    return {
        "cached_bytes": values["Cached"],
        "available_bytes": values["MemAvailable"],
        "dirty_bytes": values["Dirty"],
        "writeback_bytes": values["Writeback"],
        "swap_bytes": values["SwapTotal"] - values["SwapFree"],
    }


def reporting_backend() -> str:
    """Guest half only: proves the virtio reporting capability, not a kernel version."""
    try:
        if os.sysconf("SC_PAGE_SIZE") != 4096:
            return ""
        order = Path("/sys/module/page_reporting/parameters/page_reporting_order")
        if order.read_text().strip() != "0":
            return ""
        for device in Path("/sys/bus/virtio/devices").iterdir():
            if int((device / "device").read_text().strip(), 16) != 5:
                continue
            bits = (device / "features").read_text().strip()
            if len(bits) > 5 and bits[5] == "1":
                return BACKEND
    except (OSError, ValueError):
        pass
    return ""


def managed_process_coverage() -> bool:
    """Unleased processes under the dedicated Runtime UID invalidate idle proof.

    A detached tool can outlive its parent without inheriting a lease. Do not
    treat that process as idle, even if the task registry already completed.
    Managed guests run one Runtime process per dedicated service account.
    """
    try:
        current = os.getpid()
        allowed = set()
        while current > 0 and current not in allowed:
            allowed.add(current)
            raw = Path(f"/proc/{current}/stat").read_text()
            current = int(raw[raw.rfind(") ") + 2 :].split()[1])
        entries = list(Path("/proc").iterdir())
        if len(entries) > 10000:
            return False
        for entry in entries:
            if not entry.name.isdecimal() or int(entry.name) in allowed:
                continue
            try:
                lines = (entry / "status").read_text().splitlines()
            except FileNotFoundError:
                continue
            uid = next(line for line in lines if line.startswith("Uid:"))
            if int(uid.split()[2]) == os.geteuid():
                try:
                    if _passive_helper_probe(entry, os.getpid()):
                        continue
                except FileNotFoundError:
                    continue
                return False
        return True
    except (OSError, ValueError, IndexError, StopIteration):
        return False


class GuestMemoryHelper:
    async def stopped(self) -> bool:
        """systemd supervises the complete cgroup, including timeout-resistant children."""
        process = await asyncio.create_subprocess_exec(
            *HELPER_STATUS_COMMAND,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            output, _ = await asyncio.wait_for(process.communicate(), 2)
        except BaseException:
            if process.returncode is None:
                process.kill()
            await process.wait()
            raise
        if process.returncode != 0:
            return False
        fields = dict(line.split("=", 1) for line in output.decode().splitlines() if "=" in line)
        if fields.get("LoadState") != "loaded" or fields.get("ActiveState") not in {
            "inactive",
            "failed",
        }:
            return False
        if fields.get("Job", "").strip():
            return False
        group = fields.get("ControlGroup", "")
        if not group:
            return True
        if not group.startswith("/system.slice/") or ".." in group:
            return False
        events = Path("/sys/fs/cgroup") / group.lstrip("/") / "cgroup.events"
        try:
            return "populated 0" in events.read_text().splitlines()
        except FileNotFoundError:
            return True

    def receipt(self) -> dict:
        try:
            descriptor = os.open(RECEIPT, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
            with os.fdopen(descriptor, "rb") as source:
                info = os.fstat(source.fileno())
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_uid != 0
                    or info.st_mode & 0o022
                    or info.st_nlink != 1
                    or info.st_size > 4096
                ):
                    raise ValueError("invalid helper receipt")
                value = json.load(source)
                if not isinstance(value, dict):
                    raise ValueError("invalid helper receipt")
                return value
        except FileNotFoundError:
            return {}

    async def execute(self, operation_id: str, fence: int) -> None:
        # No sudo, shell, selected command, path, service name or properties.
        reader, writer = await asyncio.wait_for(asyncio.open_unix_connection(SOCKET), 2)
        try:
            writer.write(
                json.dumps({"operation_id": operation_id, "fence": fence}).encode() + b"\n"
            )
            await writer.drain()
            await asyncio.wait_for(reader.read(4096), 7)
        finally:
            writer.close()
            await writer.wait_closed()
