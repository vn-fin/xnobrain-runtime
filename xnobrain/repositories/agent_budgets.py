"""Runtime-owned budget preferences, isolated from Hermes state.db schema."""

import hashlib
import math
import os
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path

import yaml

from .base import StoreError


class BudgetStoreError(StoreError):
    def __init__(self, message="Budget settings unavailable", *, conflict=False):
        super().__init__(
            message,
            status=409 if conflict else 503,
            code="budget_revision_conflict" if conflict else "budget_store_unavailable",
        )


class AgentBudgetStore:
    def __init__(self, data_dir: Path):
        # Do not mkdir: caller must supply an already mounted persistent data root.
        root = Path(data_dir)
        if not root.is_dir() or root.is_symlink():
            raise BudgetStoreError()
        self.path = root / "agent_budgets.sqlite3"
        if self.path.is_symlink():
            raise BudgetStoreError()
        if not self.path.exists():
            try:
                descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(descriptor)
            except FileExistsError:
                pass
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS agent_budgets (
                    workspace_id TEXT NOT NULL, context_id TEXT NOT NULL,
                    local_agent_id TEXT NOT NULL, accounting_id TEXT NOT NULL UNIQUE,
                    weekly_usd REAL, currency TEXT NOT NULL DEFAULT 'USD',
                    policy_revision INTEGER NOT NULL DEFAULT 1,
                    revision INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                    migration_source TEXT NOT NULL,
                    PRIMARY KEY(workspace_id, context_id, local_agent_id),
                    CHECK(weekly_usd IS NULL OR weekly_usd >= 1)
                );
                CREATE TABLE IF NOT EXISTS agent_budget_migrations (
                    accounting_id TEXT PRIMARY KEY, yaml_sha256 TEXT NOT NULL,
                    original_yaml BLOB NOT NULL
                );
            """)
        self.path.chmod(0o600)

    @contextmanager
    def _connect(self):
        try:
            db = sqlite3.connect(self.path, timeout=2, isolation_level="IMMEDIATE")
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA busy_timeout=2000")
            db.execute("PRAGMA synchronous=FULL")
            try:
                with db:
                    yield db
            finally:
                db.close()
        except sqlite3.Error as error:
            raise BudgetStoreError() from error

    @staticmethod
    def _scope(*parts):
        if any(not isinstance(part, str) or not part or len(part) > 256 for part in parts):
            raise BudgetStoreError("Invalid budget scope")

    @staticmethod
    def _limit(value):
        if value is None:
            return None
        if isinstance(value, bool):
            raise BudgetStoreError("Budget must be at least $1")
        try:
            amount = float(value)
        except (TypeError, ValueError) as error:
            raise BudgetStoreError("Invalid budget") from error
        if not math.isfinite(amount) or amount < 1:
            raise BudgetStoreError("Budget must be at least $1")
        return amount

    @staticmethod
    def _public(row):
        value = dict(row)
        value["configured"] = value["weekly_usd"] is not None
        value["weekly_usd"] = value["weekly_usd"] if value["configured"] else 20.0
        return value

    def get(self, workspace, context, agent, config_path):
        self._scope(workspace, context, agent)
        try:
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                args = (workspace, context, agent)
                row = db.execute(
                    "SELECT * FROM agent_budgets WHERE workspace_id=? AND context_id=? AND local_agent_id=?",
                    args,
                ).fetchone()
                if row is None:
                    path = Path(config_path)
                    if path.is_symlink():
                        raise BudgetStoreError("Unsafe budget config path")
                    original = path.read_bytes() if path.exists() else b""
                    if len(original) > 1024 * 1024:
                        raise BudgetStoreError("Budget config is too large")
                    config = yaml.safe_load(original) or {}
                    if not isinstance(config, dict):
                        raise BudgetStoreError("Invalid budget config")
                    legacy = config.get("xnobrain_budget", {})
                    if not isinstance(legacy, dict):
                        raise BudgetStoreError("Invalid legacy budget")
                    weekly = self._limit(legacy.get("weekly_usd"))
                    identity = "agent_" + uuid.uuid4().hex
                    db.execute(
                        "INSERT INTO agent_budgets(workspace_id,context_id,local_agent_id,accounting_id,weekly_usd,migration_source) VALUES(?,?,?,?,?,?)",
                        (
                            *args,
                            identity,
                            weekly,
                            "yaml" if "xnobrain_budget" in config else "default",
                        ),
                    )
                    db.execute(
                        "INSERT INTO agent_budget_migrations VALUES(?,?,?)",
                        (identity, hashlib.sha256(original).hexdigest(), original),
                    )
                    row = db.execute(
                        "SELECT * FROM agent_budgets WHERE accounting_id=?", (identity,)
                    ).fetchone()
                return self._public(row)
        except (sqlite3.Error, OSError, yaml.YAMLError) as error:
            raise BudgetStoreError() from error

    def set(self, workspace, context, agent, weekly, revision):
        self._scope(workspace, context, agent)
        weekly = self._limit(weekly)
        try:
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                args = (workspace, context, agent)
                row = db.execute(
                    "SELECT * FROM agent_budgets WHERE workspace_id=? AND context_id=? AND local_agent_id=?",
                    args,
                ).fetchone()
                if row is None:
                    raise BudgetStoreError()
                if revision is not None and row["revision"] != revision:
                    raise BudgetStoreError("Budget changed. Reload before saving.", conflict=True)
                db.execute(
                    "UPDATE agent_budgets SET weekly_usd=?, revision=revision+1, updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE workspace_id=? AND context_id=? AND local_agent_id=?",
                    (weekly, *args),
                )
                return self._public(
                    db.execute(
                        "SELECT * FROM agent_budgets WHERE workspace_id=? AND context_id=? AND local_agent_id=?",
                        args,
                    ).fetchone()
                )
        except sqlite3.Error as error:
            raise BudgetStoreError() from error
