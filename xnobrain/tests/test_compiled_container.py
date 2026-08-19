"""Production container packaging contracts."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class CompiledContainerTests(unittest.TestCase):
    def test_runtime_endpoint_is_a_nuitka_onefile_executable(self):
        dockerfile = (ROOT / "Dockerfile.backend").read_text(encoding="utf-8")

        self.assertIn("'nuitka[onefile]==4.1.3'", dockerfile)
        self.assertIn("--mode=onefile", dockerfile)
        self.assertIn(
            "COPY --from=endpoint-builder /opt/xnobrain-dist/xnobrain-endpoint",
            dockerfile,
        )
        final_stage = dockerfile.split("FROM runtime-base\n", 1)[1]
        self.assertNotIn("COPY xnobrain ", final_stage)
        self.assertNotIn("COPY server.py ", final_stage)

    def test_container_entrypoint_starts_compiled_endpoint(self):
        entrypoint = (ROOT / "runtime" / "container-entrypoint.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("/usr/local/bin/xnobrain-endpoint &", entrypoint)
        self.assertNotIn("/opt/xnobrain/server.py", entrypoint)


if __name__ == "__main__":
    unittest.main()
