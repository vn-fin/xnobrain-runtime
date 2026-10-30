"""Read important local container usage without shelling out or listing processes."""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import socket
import stat
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
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
        memory = self._memory_metrics()
        memory_limit = memory["memory_limit_bytes"]
        instance_type = self._instance_type()
        volumes = self._storage_volumes(instance_type)
        if instance_type == "container":
            self._attach_private_storage(volumes)
        root = volumes[0]
        disk_available = all(volume["available"] for volume in volumes)
        network_rx, network_tx = self._network_usage()
        hostname = socket.gethostname()
        uptime = max(0, int(time.time() - self.started_at))
        cpus = self._cpu_limit()
        os_name, os_version = self._os_release()
        return {
            "info": {
                "id": hostname,
                "status": "running",
                "type": instance_type,
                "image": os.getenv("XNOBRAIN_RUNTIME_IMAGE", "xnobrain-runtime"),
                "created_at": datetime.fromtimestamp(self.started_at, UTC)
                .isoformat()
                .replace("+00:00", "Z"),
                "gateway": {
                    "healthy": True,
                    "port": int(os.getenv("API_SERVER_PORT", "3000")),
                },
                "resources": {
                    "cpus": str(cpus),
                    "memory": self._format_bytes(memory_limit),
                    "root_size": self._format_bytes(root["total_bytes"])
                    if root["available"]
                    else "",
                },
            },
            "metrics": {
                "cpu_percent": cpu_percent,
                "vcpu_time_ns": cpu_usage_ns,
                "uptime_seconds": uptime,
                **memory,
                "disk_usage_bytes": sum(v["used_bytes"] for v in volumes) if disk_available else 0,
                "disk_total_bytes": sum(v["total_bytes"] for v in volumes) if disk_available else 0,
                "disk_metrics_available": disk_available,
                "storage_volumes": volumes,
                "private_disk_metrics_available": all(
                    v.get("private_metrics_available", False) for v in volumes
                ),
                "private_disk_usage_bytes": (
                    sum(v["private_used_bytes"] for v in volumes)
                    if all(v.get("private_metrics_available", False) for v in volumes)
                    else None
                ),
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
            "updated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }

    @staticmethod
    def _private_storage_sample() -> dict[str, Any]:
        """Accept only a fresh, root-owned host sample for this workspace."""
        workspace = os.getenv("RUNTIME_WORKSPACE_ID", "")
        if not workspace:
            return {}
        try:
            descriptor = os.open(
                "/run/xnobrain-private-storage.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            )
            with os.fdopen(descriptor) as stream:
                metadata = os.fstat(stream.fileno())
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_uid != 0
                    or metadata.st_mode & 0o022
                    or metadata.st_size > 65536
                ):
                    return {}
                sample = json.loads(stream.read(65537))
            if not isinstance(sample, dict):
                return {}
            sampled = sample.get("sampled_at")
            if (
                sample.get("version") != 1
                or sample.get("workspace_id") != workspace
                or sample.get("source") != "lvm_thin_exclusive"
                or type(sampled) not in (int, float)
                or not -5 <= time.time() - sampled <= 180
                or not isinstance(sample.get("volumes"), dict)
            ):
                return {}
            return sample["volumes"]
        except (OSError, ValueError, TypeError):
            return {}

    @classmethod
    def _attach_private_storage(cls, volumes: list[dict[str, Any]]) -> None:
        sample = cls._private_storage_sample()
        for volume in volumes:
            measured = sample.get(volume["name"], {})
            if not isinstance(measured, dict):
                measured = {}
            private, shared = measured.get("private_used_bytes"), measured.get("shared_bytes")
            if type(private) is int and private >= 0 and type(shared) is int and shared >= 0:
                volume.update(
                    private_metrics_available=True,
                    private_used_bytes=private,
                    shared_bytes=shared,
                    private_source="lvm_thin_exclusive",
                )
            else:
                volume.setdefault("private_metrics_available", False)
                volume.setdefault("private_used_bytes", None)
                volume.setdefault("shared_bytes", None)

    @staticmethod
    def _instance_type() -> str:
        explicit = os.getenv("RUNTIME_INSTANCE_TYPE", "").strip().lower()
        if explicit in {"container", "virtual-machine", "vm"}:
            return "virtual-machine" if explicit == "vm" else explicit
        if any(
            Path(p).exists()
            for p in ("/run/systemd/container", "/.dockerenv", "/run/.containerenv")
        ):
            return "container"
        return "virtual-machine"

    @staticmethod
    def _mounts() -> list[dict[str, str]]:
        mounts = []
        try:
            for line in Path("/proc/self/mountinfo").read_text().splitlines():
                before, after = line.split(" - ", 1)
                fields, filesystem = before.split(), after.split()
                mounts.append(
                    {
                        "path": re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), fields[4]),
                        "device": fields[2],
                        "root": fields[3],
                        "type": filesystem[0],
                        "options": filesystem[2],
                    }
                )
        except (OSError, ValueError, IndexError):
            return []
        return mounts

    @staticmethod
    def _filesystem_uuid(path: Path) -> str:
        # Linux BTRFS_IOC_FS_INFO, a read-only unprivileged ioctl. fsid is the
        # 16-byte field after max_id and num_devices in the 1024-byte UAPI struct.
        import fcntl

        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            buffer = bytearray(1024)
            fcntl.ioctl(descriptor, 0x8400941F, buffer)
            return str(uuid.UUID(bytes=bytes(buffer[16:32])))
        finally:
            os.close(descriptor)

    @classmethod
    def _volume_metrics(
        cls, name: str, path: Path, mount: dict[str, str] | None, instance_type: str
    ) -> dict[str, Any]:
        volume = {
            "name": name,
            "available": False,
            "used_bytes": None,
            "total_bytes": None,
            "source": "unavailable",
        }
        try:
            if instance_type == "container" and mount and mount["type"] == "btrfs":
                options = dict(
                    option.split("=", 1) for option in mount["options"].split(",") if "=" in option
                )
                subvolume = int(options["subvolid"])
                if subvolume <= 0:
                    return volume
                qgroup = (
                    Path("/sys/fs/btrfs")
                    / cls._filesystem_uuid(path)
                    / "qgroups"
                    / f"0_{subvolume}"
                )
                flags = int((qgroup / "limit_flags").read_text().strip(), 0)
                # Match the usage counter to the enforced limit. Incus volume
                # usage reports exclusive bytes even with a referenced quota.
                if flags & 3 == 1 and not flags & 16:
                    counter, maximum = "referenced", "max_referenced"
                elif flags & 3 == 2 and not flags & 32:
                    counter, maximum = "exclusive", "max_exclusive"
                else:
                    return volume
                used = int((qgroup / counter).read_text())
                total = int((qgroup / maximum).read_text())
                source = f"btrfs_qgroup_{counter}"
                try:
                    private = int((qgroup / "exclusive").read_text())
                    referenced = int((qgroup / "referenced").read_text())
                    if 0 <= private <= referenced:
                        volume.update(
                            private_metrics_available=True,
                            private_used_bytes=private,
                            shared_bytes=referenced - private,
                            private_source="btrfs_qgroup_exclusive",
                        )
                except (OSError, ValueError):
                    # Private accounting must not change quota/preflight behavior.
                    pass
            elif instance_type == "container" and (
                not mount or mount["type"] in {"overlay", "aufs"}
            ):
                # Shared writable layers do not expose a reliable quota through
                # statvfs. Unknown is preferable to reporting the host pool.
                return volume
            else:
                usage = shutil.disk_usage(path)
                used, total, source = usage.used, usage.total, "filesystem"
            if used < 0 or total <= 0:
                return volume
            volume.update(available=True, used_bytes=used, total_bytes=total, source=source)
        except (OSError, ValueError, KeyError, ImportError):
            pass
        return volume

    @classmethod
    def storage_capacity(cls, path: Path, *, require_quota: bool = False) -> dict[str, Any]:
        """Usable headroom, bounded by both the enforced quota and filesystem."""
        unavailable = {"available": False, "free_bytes": None, "source": "unavailable"}
        try:
            usage = shutil.disk_usage(path)
            if not require_quota:
                return {"available": True, "free_bytes": usage.free, "source": "filesystem"}
            path = path.resolve()
            mount = next(
                (
                    mount
                    for mount in sorted(
                        cls._mounts(), key=lambda item: len(item["path"]), reverse=True
                    )
                    if path.is_relative_to(Path(mount["path"]))
                ),
                None,
            )
            volume = cls._volume_metrics("data", path, mount, "container")
            if not volume["available"] or not volume["source"].startswith("btrfs_qgroup_"):
                return unavailable
            return {
                "available": True,
                "free_bytes": max(0, min(usage.free, volume["total_bytes"] - volume["used_bytes"])),
                "source": volume["source"],
            }
        except (OSError, ValueError):
            return unavailable

    def _storage_volumes(self, instance_type: str) -> list[dict[str, Any]]:
        mounts = self._mounts()
        root_mount = next((m for m in mounts if m["path"] == "/"), None)
        volumes = [self._volume_metrics("root", Path("/"), root_mount, instance_type)]
        # Keep VM filesystem reporting compatible. Containers additionally
        # report a separately mounted data volume; never double-count root.
        if instance_type == "container":
            data_path = self.data_dir.resolve()
            data_mount = next(
                (
                    m
                    for m in sorted(mounts, key=lambda m: len(m["path"]), reverse=True)
                    if data_path.is_relative_to(Path(m["path"]))
                ),
                None,
            )
            if (
                data_mount
                and root_mount
                and (data_mount["device"], data_mount["root"])
                != (root_mount["device"], root_mount["root"])
            ):
                volumes.append(self._volume_metrics("data", data_path, data_mount, instance_type))
        return volumes

    def _cpu_usage(self) -> tuple[float, int]:
        usage_ns = 0
        try:
            fields = dict(
                line.split(None, 1)
                for line in Path("/sys/fs/cgroup/cpu.stat").read_text(encoding="utf-8").splitlines()
            )
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
        metrics = LocalRuntimeManager._memory_metrics()
        return metrics["memory_bytes"], metrics["memory_limit_bytes"]

    @staticmethod
    def _memory_metrics() -> dict[str, Any]:
        host = LocalRuntimeManager._host_memory_metrics()
        host_total = host["memory_limit_bytes"]
        candidates: list[tuple[Path, Path]] = []
        try:
            for line in Path("/proc/self/cgroup").read_text(encoding="utf-8").splitlines():
                hierarchy, controllers, relative = line.split(":", 2)
                relative_path = relative.lstrip("/")
                if hierarchy == "0" and not controllers:
                    base = Path("/sys/fs/cgroup")
                    root = base / relative_path
                    for directory in (root, *root.parents):
                        if directory.is_relative_to(base):
                            candidates.append(
                                (directory / "memory.current", directory / "memory.max")
                            )
                elif "memory" in controllers.split(","):
                    base = Path("/sys/fs/cgroup/memory")
                    root = base / relative_path
                    for directory in (root, *root.parents):
                        if directory.is_relative_to(base):
                            candidates.append(
                                (
                                    directory / "memory.usage_in_bytes",
                                    directory / "memory.limit_in_bytes",
                                )
                            )
        except (OSError, ValueError):
            pass
        candidates.extend(
            [
                (
                    Path("/sys/fs/cgroup/memory.current"),
                    Path("/sys/fs/cgroup/memory.max"),
                ),
                (
                    Path("/sys/fs/cgroup/memory/memory.usage_in_bytes"),
                    Path("/sys/fs/cgroup/memory/memory.limit_in_bytes"),
                ),
            ]
        )
        seen: set[tuple[Path, Path]] = set()
        allocation: tuple[int, int] | None = None
        for current_path, limit_path in candidates:
            if (current_path, limit_path) in seen:
                continue
            seen.add((current_path, limit_path))
            try:
                used = max(0, int(current_path.read_text(encoding="utf-8").strip()))
                raw_limit = limit_path.read_text(encoding="utf-8").strip()
                if raw_limit == "max":
                    continue
                limit = int(raw_limit)
                # Cgroup v1 represents an unlimited hierarchy with a very large
                # sentinel value rather than the v2 "max" token.
                if limit <= 0 or limit > host_total * 16:
                    continue
                reading = (min(used, limit), limit)
                # A finite child may still inherit a smaller parent quota.
                # At equal quotas, use the aggregate usage of the parent.
                if (
                    allocation is None
                    or limit < allocation[1]
                    or (limit == allocation[1] and reading[0] > allocation[0])
                ):
                    allocation = reading
            except (OSError, ValueError):
                continue
        if allocation is None:
            return host
        used, limit = allocation
        return {
            "memory_bytes": used,
            "memory_limit_bytes": limit,
            "memory_available_bytes": limit - used,
            "memory_occupied_bytes": used,
        }

    @staticmethod
    def _host_memory_usage() -> tuple[int, int]:
        metrics = LocalRuntimeManager._host_memory_metrics()
        return metrics["memory_bytes"], metrics["memory_limit_bytes"]

    @staticmethod
    def _host_memory_metrics() -> dict[str, Any]:
        values: dict[str, int] = {}
        try:
            for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
                key, raw = line.split(":", 1)
                amount = raw.strip().split()
                if amount:
                    values[key] = int(amount[0]) * 1024
        except (OSError, ValueError):
            values = {}
        total = values.get("MemTotal", 0)
        if not total:
            total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        available = values.get("MemAvailable")
        if available is None:
            # Older kernels omit MemAvailable. This is the conventional procps
            # approximation, with reclaimable slab adjusted for shared memory.
            available = (
                values.get("MemFree", 0)
                + values.get("Buffers", 0)
                + values.get("Cached", 0)
                + values.get("SReclaimable", 0)
                - values.get("Shmem", 0)
            )
        available = max(0, min(total, available))
        metrics = {
            "memory_bytes": max(0, total - available),
            "memory_limit_bytes": total,
            "memory_available_bytes": available,
        }
        if "MemFree" in values:
            free = max(0, min(total, values["MemFree"]))
            metrics["memory_occupied_bytes"] = total - free
        return metrics

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
