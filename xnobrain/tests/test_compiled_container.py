"""Production container packaging contracts."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class CompiledContainerTests(unittest.TestCase):
    def test_runtime_endpoint_is_source_only(self):
        dockerfile = (ROOT / "Dockerfile.backend").read_text(encoding="utf-8")

        self.assertIn("FROM runtime-base AS runtime", dockerfile)
        self.assertIn("WORKDIR /opt/xnobrain-app", dockerfile)
        self.assertIn("COPY xnobrain ./xnobrain", dockerfile)
        self.assertIn("COPY server.py ./server.py", dockerfile)
        self.assertNotIn("BUILD_MODE", dockerfile)
        self.assertNotIn("nuitka", dockerfile.lower())
        self.assertNotIn("app.so", dockerfile)

    def test_hermes_installer_download_retries_transient_failures(self):
        dockerfile = (ROOT / "Dockerfile.backend").read_text(encoding="utf-8")

        self.assertIn("--retry 8 --retry-all-errors --retry-delay 3", dockerfile)
        self.assertIn("scripts/install.sh?ref=${HERMES_COMMIT}", dockerfile)
        self.assertIn("-o /tmp/hermes-install.sh", dockerfile)
        self.assertNotIn('install.sh" \\\n      |', dockerfile)

    def test_incus_image_does_not_declare_oci_data_volume(self):
        dockerfile = (ROOT / "Dockerfile.backend").read_text(encoding="utf-8")
        active = "\n".join(
            line for line in dockerfile.splitlines() if not line.lstrip().startswith("#")
        )

        self.assertNotIn('VOLUME ["/opt/data"]', active)
        self.assertIn("install -d -m 0700 /opt/data", dockerfile)

    def test_container_entrypoint_starts_source_endpoint(self):
        entrypoint = (ROOT / "runtime" / "container-entrypoint.sh").read_text(encoding="utf-8")

        self.assertIn('exec "$hermes_python" /opt/xnobrain-app/server.py', entrypoint)
        self.assertNotIn("app.so", entrypoint)
        self.assertNotIn("XNOBRAIN_BUILD_MODE", entrypoint)

    def test_native_agent_cli_receives_current_router_key(self):
        dockerfile = (ROOT / "Dockerfile.backend").read_text(encoding="utf-8")
        entrypoint = (ROOT / "runtime" / "container-entrypoint.sh").read_text(
            encoding="utf-8"
        )
        launcher = (ROOT / "runtime" / "agent-cli.sh").read_text(encoding="utf-8")

        self.assertIn("COPY runtime/agent-cli.sh /usr/local/bin/agent", dockerfile)
        self.assertIn(
            "export RUNTIME_LLM_API_KEY_FILE=/run/xnobrain-runtime/router-api-key",
            entrypoint,
        )
        self.assertIn('chmod 0600 "$key_file"', entrypoint)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_hermes = root / "hermes"
            fake_hermes.write_text(
                '#!/usr/bin/env bash\n'
                '[[ "$RUNTIME_LLM_API_KEY" == "$EXPECTED_KEY" && "$1" == "-p" ]]\n',
                encoding="utf-8",
            )
            fake_hermes.chmod(0o755)
            agent = root / "agent"
            agent.write_text(
                launcher.replace(
                    "/usr/local/bin/xnobrain-link-native-profiles", "/usr/bin/true"
                ).replace("/usr/local/lib/hermes-agent/venv/bin/hermes", str(fake_hermes)),
                encoding="utf-8",
            )
            agent.chmod(0o755)
            key_file = root / "router-api-key"
            key_file.write_text("current-key\n", encoding="utf-8")

            def run(expected_key: str, file_path: str) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [str(agent), "-p", "poem"],
                    env={
                        "PATH": os.environ.get("PATH", ""),
                        "RUNTIME_LLM_API_KEY": "stale-key",
                        "RUNTIME_LLM_API_KEY_FILE": file_path,
                        "EXPECTED_KEY": expected_key,
                    },
                    capture_output=True,
                    text=True,
                    check=False,
                )

            self.assertEqual(run("current-key", str(key_file)).returncode, 0)
            key_file.write_text("rotated-key\n", encoding="utf-8")
            self.assertEqual(run("rotated-key", str(key_file)).returncode, 0)
            self.assertEqual(run("", str(root / "missing-key")).returncode, 0)
            self.assertEqual(run("stale-key", "").returncode, 0)

    def test_cli_profiles_link_to_runtime_agents_without_moving_existing_data(self):
        linker = ROOT / "runtime" / "link-native-profiles.sh"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "big-brother"
            root.mkdir()
            profiles = Path(directory) / "agents"
            profiles.mkdir()
            environment = {
                "PATH": os.environ.get("PATH", ""),
                "HERMES_ROOT_PROFILE": str(root),
                "HERMES_PROFILES_ROOT": str(profiles),
            }

            subprocess.run(["bash", str(linker)], env=environment, check=True)
            self.assertEqual((root / "profiles").resolve(), profiles.resolve())

            (root / "profiles").unlink()
            nested = root / "profiles" / "poem"
            nested.mkdir(parents=True)
            (nested / "state.db").write_text("existing data", encoding="utf-8")
            subprocess.run(["bash", str(linker)], env=environment, check=True)
            self.assertEqual((nested / "state.db").read_text(encoding="utf-8"), "existing data")
            self.assertFalse((profiles / "poem").exists())

    def test_runtime_uses_only_a_generic_central_router_client(self):
        entrypoint = (ROOT / "runtime" / "container-entrypoint.sh").read_text(encoding="utf-8")
        dockerfile = (ROOT / "Dockerfile.backend").read_text(encoding="utf-8")
        compose = (ROOT / "docker-compose.yaml").read_text(encoding="utf-8")
        systemd_target = (ROOT / "deploy" / "systemd" / "xnobrain.target").read_text(
            encoding="utf-8"
        )
        installer = (ROOT / "scripts" / "install-linux.sh").read_text(encoding="utf-8")

        self.assertIn("RUNTIME_LLM_ROUTER_URL is required", entrypoint)
        self.assertIn('RUNTIME_LLM_API_KEY="${RUNTIME_LLM_API_KEY:-}"', entrypoint)
        self.assertNotIn("RUNTIME_LLM_API_KEY is required", entrypoint)
        self.assertIn('exec "$hermes_python" /opt/xnobrain-app/server.py', entrypoint)
        self.assertNotIn("omniroute serve", entrypoint)
        self.assertNotIn("omniroute@", dockerfile)
        self.assertNotIn("20128", dockerfile)
        self.assertNotIn("omniroute:", compose)
        self.assertNotIn("xnobrain-omniroute.service", systemd_target)
        self.assertNotIn("xnobrain-router.service", systemd_target)
        self.assertNotIn("9router@", installer)
        self.assertFalse((ROOT / "deploy" / "systemd" / "xnobrain-omniroute.service").exists())
        self.assertFalse((ROOT / "deploy" / "systemd" / "xnobrain-router.service").exists())
        self.assertFalse((ROOT / "runtime" / "prepare-omniroute-auth.sh").exists())
        self.assertFalse((ROOT / "third_party_licenses" / "provider-runtime.LICENSE").exists())
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
        installer = (ROOT / "scripts" / "install-linux.sh").read_text(encoding="utf-8")

        active_dockerfile = "\n".join(
            line for line in dockerfile.splitlines() if not line.lstrip().startswith("#")
        )
        active_installer = "\n".join(
            line for line in installer.splitlines() if not line.lstrip().startswith("#")
        )

        self.assertGreaterEqual(active_dockerfile.count("--mount=type=cache,target=/root/.npm"), 3)
        self.assertIn("--mount=type=cache,target=/root/.cache/uv", active_dockerfile)
        self.assertIn("rm -rf /opt/hermes-build-home", active_dockerfile)
        for standalone_cli in (
            "@openai/codex",
            "@anthropic-ai/claude-code",
            "opencode-ai",
            "opencode@",
        ):
            self.assertNotIn(standalone_cli, dockerfile.lower())
            self.assertNotIn(standalone_cli, installer.lower())

    def test_incus_container_enables_dhcp_dns_resolution(self):
        dockerfile = (ROOT / "Dockerfile.backend").read_text(encoding="utf-8")

        self.assertIn("systemd-resolved", dockerfile)
        self.assertIn("multi-user.target.wants/systemd-resolved.service", dockerfile)
        self.assertIn("sysinit.target.wants/xnobrain-resolv-conf.service", dockerfile)

        service = (ROOT / "runtime" / "xnobrain-resolv-conf.service").read_text(encoding="utf-8")
        script = (ROOT / "runtime" / "prepare-resolv-conf.sh").read_text(encoding="utf-8")
        self.assertIn("Before=systemd-resolved.service", service)
        self.assertIn("ln -sfn", script)
        self.assertIn("/run/systemd/resolve/stub-resolv.conf", script)


if __name__ == "__main__":
    unittest.main()
