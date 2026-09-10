"""First-party budget persistence; no provider calls or live migration."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from xnobrain.repositories.agent_budgets import AgentBudgetStore, BudgetStoreError


class AgentBudgetStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.profile = self.root / "agent-a"
        self.profile.mkdir()
        self.yaml = self.profile / "config.yaml"
        self.yaml.write_text("xnobrain_budget:\n  weekly_usd: 35\n  currency: USD\nother: keep\n")
        self.store = AgentBudgetStore(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_import_once_preserves_yaml_and_identity(self):
        original = self.yaml.read_bytes()
        first = self.store.get("workspace-a", "personal", "agent-a", self.yaml)
        self.assertEqual(first["weekly_usd"], 35)
        self.assertEqual(first["migration_source"], "yaml")
        self.store.set("workspace-a", "personal", "agent-a", 12, first["revision"])
        self.yaml.write_text("xnobrain_budget:\n  weekly_usd: 999\n")
        second = self.store.get("workspace-a", "personal", "agent-a", self.yaml)
        self.assertEqual(second["weekly_usd"], 12)
        self.assertEqual(second["accounting_id"], first["accounting_id"])
        with sqlite3.connect(self.store.path) as db:
            self.assertEqual(db.execute("SELECT original_yaml FROM agent_budget_migrations").fetchone()[0], original)

    def test_null_resets_default_and_conflicting_revision_rejected(self):
        first = self.store.get("w", "personal", "agent-a", self.yaml)
        second = self.store.set("w", "personal", "agent-a", None, first["revision"])
        self.assertEqual(second["weekly_usd"], 20)
        self.assertFalse(second["configured"])
        with self.assertRaises(BudgetStoreError):
            self.store.set("w", "personal", "agent-a", 30, first["revision"])

    def test_scopes_and_clones_do_not_share_identity(self):
        a = self.store.get("w", "personal", "agent-a", self.yaml)
        b = self.store.get("other-w", "personal", "agent-a", self.yaml)
        clone = self.store.get("w", "personal", "clone", self.yaml)
        self.assertEqual(len({a["accounting_id"], b["accounting_id"], clone["accounting_id"]}), 3)

    def test_invalid_yaml_never_imports_as_default(self):
        self.yaml.write_text("xnobrain_budget: [broken")
        with self.assertRaises(BudgetStoreError):
            self.store.get("w", "personal", "agent-a", self.yaml)

    def test_missing_data_directory_is_not_created(self):
        with self.assertRaises(BudgetStoreError):
            AgentBudgetStore(self.root / "missing")
        self.assertFalse((self.root / "missing").exists())

    def test_invalid_limits_rejected(self):
        self.store.get("w", "personal", "agent-a", self.yaml)
        for amount in [0, -1, float("nan"), float("inf")]:
            with self.assertRaises(BudgetStoreError):
                self.store.set("w", "personal", "agent-a", amount, None)
