"""Working overlays must not hide durable product instructions."""

import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from xnobrain.integrations.hermes import AgentManager


class WorkspaceContextTests(unittest.TestCase):
    def test_both_context_files_for_named_and_root_sessions(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            manager = AgentManager(
                root_profile=root / "root",
                profiles_root=root / "profiles",
                legacy_agents_root=root / "legacy",
                profile_template=Path(__file__).resolve().parents[2] / "runtime/profile-templates",
            )
            manager.create_agent({"name": "writer"})
            for name in ("writer", "big-brother"):
                profile = manager._profile_dir(name)
                workspace = profile / "workspace"
                manager._ensure_workspace_agents(profile, workspace)
                self.assertTrue((workspace / "HERMES.md").is_file())
                self.assertTrue((workspace / "AGENTS.md").is_file())
                overlay = (workspace / "HERMES.md").read_text()
                agent = SimpleNamespace(
                    _build_system_prompt=lambda _: overlay,
                    _cached_system_prompt=overlay,
                    _cached_system_prompt_static="",
                )
                manager._apply_runtime_help_guidance_override(agent, profile, workspace)
                for prompt in (agent._build_system_prompt(None), agent._cached_system_prompt):
                    self.assertIn("## Source citations", prompt)
                    self.assertIn("## Python environments", prompt)
                    self.assertIn(f"/opt/data/python/.{name}-venv", prompt)
                    self.assertEqual("# Agent workspace" in prompt, name == "writer")
                (workspace / "HERMES.md").write_text("Custom working rules")
                manager._ensure_workspace_agents(profile, workspace)
                self.assertEqual((workspace / "HERMES.md").read_text(), "Custom working rules")

            profile = manager._profile_dir("writer")
            session = manager.create_conversation("writer", {"title": "Resume"})
            session_id = session["conversation"]["id"]
            for _ in range(2):
                manager._override_stored_runtime_help_guidance(
                    profile, session_id, profile / "workspace"
                )
            with sqlite3.connect(profile / "state.db") as connection:
                prompt = connection.execute(
                    "SELECT system_prompt FROM sessions WHERE id = ?", (session_id,)
                ).fetchone()[0]
            self.assertEqual(prompt.count("<!-- runtime-workspace-context -->"), 1)
            self.assertIn("Custom working rules", prompt)
            self.assertIn("## Source citations", prompt)
            self.assertIn("user deliverables must still stay here", prompt)

    def test_template_keeps_environment_rules_out_of_agents(self):
        templates = Path(__file__).resolve().parents[2] / "runtime/profile-templates"
        agents = (templates / "AGENTS.md").read_text()
        overlay = (templates / "HERMES.md").read_text()
        for word in ("XNOBrain", "Hermes", "9router", "uv venv", "/opt/data/python"):
            self.assertNotIn(word, agents)
        self.assertIn('uv --no-cache venv "$VENV"', overlay)
        self.assertIn('uv --no-cache pip install --python "$VENV/bin/python"', overlay)
        self.assertIn("Never create `.venv` or `venv`", overlay)

    def test_entrypoint_provisions_persistent_root_without_replacing_runtime(self):
        repository = Path(__file__).resolve().parents[2]
        entrypoint = (repository / "runtime/container-entrypoint.sh").read_text()
        self.assertIn("install -d -m 0700 /opt/data/python", entrypoint)
        self.assertIn("[[ -w /opt/data/python ]]", entrypoint)
        self.assertIn("/usr/local/lib/hermes-agent/venv/bin/python", entrypoint)
        self.assertNotIn("install -d -m 0700 /opt/python", entrypoint)

    def test_context_refresh_replaces_old_rules_and_ignores_symlinks(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            manager = AgentManager(root_profile=root / "root", profiles_root=root / "profiles")
            manager.create_agent({"name": "writer"})
            profile = manager._profile_dir("writer")
            workspace = profile / "workspace"
            first = manager._workspace_context(profile, workspace)
            prompt = manager._with_workspace_context("Identity", first)
            (workspace / "HERMES.md").write_text("Updated rules")
            second = manager._workspace_context(profile, workspace)
            prompt = manager._with_workspace_context(prompt, second)
            self.assertIn("Updated rules", prompt)
            self.assertNotIn("## Python environments", prompt)
            self.assertEqual(prompt.count("<!-- runtime-workspace-context -->"), 1)
            (workspace / "AGENTS.md").unlink()
            private = root / "private"
            private.write_text("DO NOT LOAD")
            (workspace / "AGENTS.md").symlink_to(private)
            self.assertNotIn("DO NOT LOAD", manager._workspace_context(profile, workspace))
