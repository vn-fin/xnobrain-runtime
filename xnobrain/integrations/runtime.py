"""Read important local container usage without shelling out or listing processes."""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import platform
import shutil
import socket
import threading
import time
from typing import Any


class LocalRuntimeManager:
    """Small adapter over Linux cgroup and proc files for the local runtime."""

    def __init__(self, *, data_dir: str | Path | None = None):
        self.data_dir = Path(data_dir or os.getenv("DATA_DIR") or "/opt/data")
        self.started_at = time.time()
        self._previous_cpu: tuple[float, int] | None = None
        self._lock = threading.Lock()

    def detail(self) -> dict[str, Any]:
        cpu_percent, cpu_usage_ns = self._cpu_usage()
        memory_used, memory_limit = self._memory_usage()
        # Docker's storage quota applies to the container writable layer. Read
        # the root filesystem so the API reports that enforced limit instead
        # of the host capacity backing the persistent profile volume.
        disk = shutil.disk_usage(Path("/"))
        network_rx, network_tx = self._network_usage()
        hostname = socket.gethostname()
        uptime = max(0, int(time.time() - self.started_at))
        cpus = self._cpu_limit()
        os_name, os_version = self._os_release()
        return {
            "info": {
                "id": hostname,
                "status": "running",
                "type": "container",
                "image": os.getenv("XNOBRAIN_RUNTIME_IMAGE", "xnobrain-hermes-runtime"),
                "created_at": datetime.fromtimestamp(self.started_at, timezone.utc).isoformat().replace("+00:00", "Z"),
                "gateway": {"healthy": True, "port": int(os.getenv("API_SERVER_PORT", "8642"))},
                "resources": {
                    "cpus": str(cpus),
                    "memory": self._format_bytes(memory_limit),
                    "root_size": self._format_bytes(disk.total),
                },
            },
            "metrics": {
                "cpu_percent": cpu_percent,
                "vcpu_time_ns": cpu_usage_ns,
                "uptime_seconds": uptime,
                "memory_bytes": memory_used,
                "memory_limit_bytes": memory_limit,
                "memory_available_bytes": max(0, memory_limit - memory_used),
                "disk_usage_bytes": disk.used,
                "disk_total_bytes": disk.total,
                "net_rx_bytes": network_rx,
                "net_tx_bytes": network_tx,
            },
            "system": {
                "cpu_percent": cpu_percent,
                "os": {
                    "hostname": hostname,
                    "os": os_name,
                    "os_version": os_version,
                    "kernel_version": platform.release(),
                },
            },
            "health": {
                "healthy": True,
                "status_code": 200,
                "endpoint": "/xnobrain/api/runtime/v1/health",
            },
            "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }

    def _cpu_usage(self) -> tuple[float, int]:
        usage_ns = 0
        try:
            fields = dict(line.split(None, 1) for line in Path("/sys/fs/cgroup/cpu.stat").read_text(encoding="utf-8").splitlines())
            usage_ns = int(fields.get("usage_usec", "0")) * 1_000
        except (OSError, ValueError):
            usage_ns = time.process_time_ns()
        now = time.monotonic()
        with self._lock:
            previous = self._previous_cpu
            self._previous_cpu = (now, usage_ns)
        if previous is None or now <= previous[0] or usage_ns < previous[1]:
            return 0.0, usage_ns
        elapsed = now - previous[0]
        used = (usage_ns - previous[1]) / 1_000_000_000
        percent = used / elapsed / max(1.0, self._cpu_limit()) * 100
        return round(max(0.0, min(100.0, percent)), 2), usage_ns

    @staticmethod
    def _cpu_limit() -> float:
        try:
            quota, period = Path("/sys/fs/cgroup/cpu.max").read_text(encoding="utf-8").split()
            if quota != "max":
                return max(1.0, int(quota) / int(period))
        except (OSError, ValueError, ZeroDivisionError):
            pass
        return float(os.cpu_count() or 1)

    @staticmethod
    def _memory_usage() -> tuple[int, int]:
        try:
            used = int(Path("/sys/fs/cgroup/memory.current").read_text(encoding="utf-8").strip())
            raw_limit = Path("/sys/fs/cgroup/memory.max").read_text(encoding="utf-8").strip()
            if raw_limit != "max":
                return used, int(raw_limit)
        except (OSError, ValueError):
            used = 0
        page_size = os.sysconf("SC_PAGE_SIZE")
        total = page_size * os.sysconf("SC_PHYS_PAGES")
        return used, total

    @staticmethod
    def _network_usage() -> tuple[int, int]:
        received = transmitted = 0
        try:
            lines = Path("/proc/net/dev").read_text(encoding="utf-8").splitlines()[2:]
            for line in lines:
                interface, values = line.split(":", 1)
                if interface.strip() == "lo":
                    continue
                fields = values.split()
                received += int(fields[0])
                transmitted += int(fields[8])
        except (OSError, ValueError, IndexError):
            return 0, 0
        return received, transmitted

    @staticmethod
    def _os_release() -> tuple[str, str]:
        values: dict[str, str] = {}
        try:
            for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
                if "=" in line:
                    key, value = line.split("=", 1)
                    values[key] = value.strip().strip('"')
        except OSError:
            pass
        return values.get("NAME", platform.system()), values.get("VERSION_ID", "")

    @staticmethod
    def _format_bytes(value: int) -> str:
        amount = float(value)
        for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
            if amount < 1024 or unit == "TiB":
                return f"{amount:.0f} {unit}" if unit == "B" else f"{amount:.1f} {unit}"
            amount /= 1024
        return f"{value} B"
