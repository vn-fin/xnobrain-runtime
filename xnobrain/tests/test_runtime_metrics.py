from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

_RUNTIME_SPEC = importlib.util.spec_from_file_location(
    "xnobrain_runtime_metrics_module",
    Path(__file__).parents[1] / "integrations" / "runtime.py",
)
assert _RUNTIME_SPEC and _RUNTIME_SPEC.loader
_RUNTIME_MODULE = importlib.util.module_from_spec(_RUNTIME_SPEC)
_RUNTIME_SPEC.loader.exec_module(_RUNTIME_MODULE)
LocalRuntimeManager = _RUNTIME_MODULE.LocalRuntimeManager


class RuntimeMemoryMetricTests(unittest.TestCase):
    @staticmethod
    def _reader(files: dict[str, str]):
        def read_text(path: Path, *args, **kwargs) -> str:
            try:
                return files[str(path)]
            except KeyError as exc:
                raise FileNotFoundError(str(path)) from exc

        return read_text

    def test_reads_finite_v2_limit_from_the_process_cgroup(self) -> None:
        files = {
            "/proc/meminfo": "MemTotal: 8000 kB\nMemAvailable: 3000 kB\n",
            "/proc/self/cgroup": "0::/user.slice/runtime.scope\n",
            "/sys/fs/cgroup/user.slice/runtime.scope/memory.current": "2097152\n",
            "/sys/fs/cgroup/user.slice/runtime.scope/memory.max": "8388608\n",
        }
        with patch.object(Path, "read_text", autospec=True, side_effect=self._reader(files)):
            self.assertEqual(LocalRuntimeManager._memory_usage(), (2_097_152, 8_388_608))

    def test_v2_unlimited_uses_host_available_memory(self) -> None:
        files = {
            "/proc/meminfo": "MemTotal: 8000 kB\nMemAvailable: 3000 kB\n",
            "/proc/self/cgroup": "0::/runtime.scope\n",
            "/sys/fs/cgroup/runtime.scope/memory.current": "1048576\n",
            "/sys/fs/cgroup/runtime.scope/memory.max": "max\n",
        }
        with patch.object(Path, "read_text", autospec=True, side_effect=self._reader(files)):
            self.assertEqual(LocalRuntimeManager._memory_usage(), (5_120_000, 8_192_000))

    def test_v2_unlimited_leaf_uses_allocated_parent_memory(self) -> None:
        files = {
            "/proc/meminfo": "MemTotal: 16000000 kB\nMemAvailable: 12000000 kB\n",
            "/proc/self/cgroup": "0::/workspace/service\n",
            "/sys/fs/cgroup/workspace/service/memory.current": "1048576\n",
            "/sys/fs/cgroup/workspace/service/memory.max": "max\n",
            "/sys/fs/cgroup/workspace/memory.current": "4294967296\n",
            "/sys/fs/cgroup/workspace/memory.max": "6442450944\n",
        }
        with patch.object(Path, "read_text", autospec=True, side_effect=self._reader(files)):
            self.assertEqual(LocalRuntimeManager._memory_usage(), (4 << 30, 6 << 30))

    def test_v1_unlimited_leaf_uses_allocated_parent_memory(self) -> None:
        files = {
            "/proc/meminfo": "MemTotal: 16000000 kB\nMemAvailable: 12000000 kB\n",
            "/proc/self/cgroup": "5:memory:/workspace/service\n",
            "/sys/fs/cgroup/memory/workspace/service/memory.usage_in_bytes": "1048576\n",
            "/sys/fs/cgroup/memory/workspace/service/memory.limit_in_bytes": str(1 << 63),
            "/sys/fs/cgroup/memory/workspace/memory.usage_in_bytes": "4294967296\n",
            "/sys/fs/cgroup/memory/workspace/memory.limit_in_bytes": "6442450944\n",
        }
        with patch.object(Path, "read_text", autospec=True, side_effect=self._reader(files)):
            self.assertEqual(LocalRuntimeManager._memory_usage(), (4 << 30, 6 << 30))

    def test_smaller_parent_quota_overrides_finite_child_limit(self) -> None:
        files = {
            "/proc/meminfo": "MemTotal: 16000000 kB\nMemAvailable: 12000000 kB\n",
            "/proc/self/cgroup": "0::/workspace/service\n",
            "/sys/fs/cgroup/workspace/service/memory.current": "1048576\n",
            "/sys/fs/cgroup/workspace/service/memory.max": str(8 << 30),
            "/sys/fs/cgroup/workspace/memory.current": str(4 << 30),
            "/sys/fs/cgroup/workspace/memory.max": str(6 << 30),
        }
        with patch.object(Path, "read_text", autospec=True, side_effect=self._reader(files)):
            self.assertEqual(LocalRuntimeManager._memory_usage(), (4 << 30, 6 << 30))

    def test_equal_parent_quota_uses_all_workspace_services(self) -> None:
        files = {
            "/proc/meminfo": "MemTotal: 16000000 kB\nMemAvailable: 12000000 kB\n",
            "/proc/self/cgroup": "0::/workspace/service\n",
            "/sys/fs/cgroup/workspace/service/memory.current": "1048576\n",
            "/sys/fs/cgroup/workspace/service/memory.max": str(6 << 30),
            "/sys/fs/cgroup/workspace/memory.current": str(4 << 30),
            "/sys/fs/cgroup/workspace/memory.max": str(6 << 30),
        }
        with patch.object(Path, "read_text", autospec=True, side_effect=self._reader(files)):
            self.assertEqual(LocalRuntimeManager._memory_usage(), (4 << 30, 6 << 30))

    def test_missing_cgroup_files_fall_back_to_proc_meminfo(self) -> None:
        files = {
            "/proc/meminfo": "MemTotal: 16000 kB\nMemAvailable: 6000 kB\n",
            "/proc/self/cgroup": "0::/missing.scope\n",
        }
        with patch.object(Path, "read_text", autospec=True, side_effect=self._reader(files)):
            self.assertEqual(LocalRuntimeManager._memory_usage(), (10_240_000, 16_384_000))

    def test_proc_fallback_approximates_available_on_older_kernels(self) -> None:
        files = {
            "/proc/meminfo": (
                "MemTotal: 1000 kB\nMemFree: 100 kB\nBuffers: 50 kB\n"
                "Cached: 200 kB\nSReclaimable: 25 kB\nShmem: 5 kB\n"
            ),
            "/proc/self/cgroup": "0::/missing.scope\n",
        }
        with patch.object(Path, "read_text", autospec=True, side_effect=self._reader(files)):
            self.assertEqual(LocalRuntimeManager._memory_usage(), (645_120, 1_024_000))


if __name__ == "__main__":
    unittest.main()
