from __future__ import annotations

import unittest
import importlib.util
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
