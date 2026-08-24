"""Production container packaging contracts."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class CompiledContainerTests(unittest.TestCase):
    def test_runtime_compiles_only_the_first_party_package(self):
        dockerfile = (ROOT / "Dockerfile.backend").read_text(encoding="utf-8")

        self.assertIn("'nuitka==4.1.3'", dockerfile)
        self.assertIn("--mode=package", dockerfile)
        self.assertIn("--lto=no", dockerfile)
        self.assertIn(
            "COPY --from=endpoint-builder /opt/xnobrain-dist/xnobrain*.so "
            "/opt/xnobrain-compiled/",
            dockerfile,
        )
        self.assertIn("runtime/compiled-endpoint.sh /usr/local/bin/app.so", dockerfile)
        self.assertNotIn("BUILD_MODE", dockerfile)
        self.assertNotIn("--mode=onefile", dockerfile)
        self.assertNotIn("--include-package=hermes_cli", dockerfile)
        final_stage = dockerfile.split("FROM runtime-base AS runtime\n", 1)[1]
        self.assertNotIn("COPY xnobrain ", final_stage)
        self.assertNotIn("COPY server.py ", final_stage)

    def test_container_entrypoint_starts_compiled_endpoint(self):
        entrypoint = (ROOT / "runtime" / "container-entrypoint.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("exec /usr/local/bin/app.so", entrypoint)
        self.assertNotIn("/opt/xnobrain/server.py", entrypoint)

    def test_runtime_uses_only_a_generic_central_router_client(self):
        entrypoint = (ROOT / "runtime" / "container-entrypoint.sh").read_text(
            encoding="utf-8"
        )
        dockerfile = (ROOT / "Dockerfile.backend").read_text(encoding="utf-8")
        compose = (ROOT / "docker-compose.yaml").read_text(encoding="utf-8")
        systemd_target = (ROOT / "deploy" / "systemd" / "xnobrain.target").read_text(
            encoding="utf-8"
        )
        installer = (ROOT / "scripts" / "install-linux.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("RUNTIME_LLM_ROUTER_URL is required", entrypoint)
        self.assertIn("RUNTIME_LLM_WORKLOAD_TOKEN is required", entrypoint)
        self.assertIn("exec /usr/local/bin/app.so", entrypoint)
        self.assertNotIn("omniroute serve", entrypoint)
        self.assertNotIn("omniroute@", dockerfile)
        self.assertNotIn("20128", dockerfile)
        self.assertNotIn("omniroute:", compose)
        self.assertNotIn("xnobrain-omniroute.service", systemd_target)
        self.assertNotIn("9router@", installer)
        self.assertFalse(
            (ROOT / "deploy" / "systemd" / "xnobrain-omniroute.service").exists()
        )
        self.assertFalse((ROOT / "runtime" / "prepare-omniroute-auth.sh").exists())
        self.assertFalse(
            (ROOT / "third_party_licenses" / "provider-runtime.LICENSE").exists()
        )
        for legacy_module in (
            "omniroute.py",
            "nine_router.py",
            "nine_router_support.py",
            "nine_router_transport.py",
        ):
            self.assertFalse((ROOT / "xnobrain" / "integrations" / legacy_module).exists())
        self.assertTrue((ROOT / "xnobrain" / "integrations" / "llm_router.py").is_file())

    def test_runtime_keeps_office_conversion_without_unused_servers(self):
        dockerfile = (ROOT / "Dockerfile.backend").read_text(encoding="utf-8")
        package_block = dockerfile.split("apt-get install -y --no-install-recommends", 1)[1]
        package_block = package_block.split("&& install -d /etc/fonts/conf.d", 1)[0]

        for package in (
            "libreoffice-writer",
            "libreoffice-calc",
            "libreoffice-impress",
            "python3-uno",
            "weasyprint",
        ):
            self.assertIn(package, package_block)
        for package in (
            "postgresql ",
            "postgresql-contrib",
            "redis-server",
            "redis-tools",
            "wkhtmltopdf",
        ):
            self.assertNotIn(package, package_block)

    def test_runtime_packages_required_node_tools_in_separate_layers(self):
        dockerfile = (ROOT / "Dockerfile.backend").read_text(encoding="utf-8")
        installer = (ROOT / "scripts" / "install-linux.sh").read_text(
            encoding="utf-8"
        )

        active_dockerfile = "\n".join(
            line for line in dockerfile.splitlines() if not line.lstrip().startswith("#")
        )
        active_installer = "\n".join(
            line for line in installer.splitlines() if not line.lstrip().startswith("#")
        )

        self.assertGreaterEqual(
            active_dockerfile.count("--mount=type=cache,target=/root/.npm"), 3
        )
        self.assertIn("--mount=type=cache,target=/root/.cache/uv", active_dockerfile)
        self.assertIn("rm -rf /opt/hermes-build-home", active_dockerfile)
        self.assertNotIn("@openai/codex", active_dockerfile)
        self.assertNotIn("@anthropic-ai/claude-code", active_dockerfile)
        self.assertNotIn("@openai/codex", active_installer)
        self.assertNotIn("@anthropic-ai/claude-code", active_installer)

    def test_incus_container_enables_dhcp_dns_resolution(self):
        dockerfile = (ROOT / "Dockerfile.backend").read_text(encoding="utf-8")

        self.assertIn("systemd-resolved", dockerfile)
        self.assertIn("multi-user.target.wants/systemd-resolved.service", dockerfile)
        self.assertIn("sysinit.target.wants/xnobrain-resolv-conf.service", dockerfile)

        service = (ROOT / "runtime" / "xnobrain-resolv-conf.service").read_text(
            encoding="utf-8"
        )
        script = (ROOT / "runtime" / "prepare-resolv-conf.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("Before=systemd-resolved.service", service)
        self.assertIn("ln -sfn", script)
        self.assertIn("/run/systemd/resolve/stub-resolv.conf", script)


if __name__ == "__main__":
    unittest.main()
