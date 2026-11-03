"""Workspace-local project metadata, separate from portable agent profiles."""

from __future__ import annotations

import os
import sqlite3
import stat
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime

from .base import StoreError
from .runtime_update_gate import WorkspaceActivity


def fail(code: str, status: int) -> None:
    raise StoreError(code.replace("_", " "), status=status, code=code)


class AgentProjectsRepository:
    def __init__(self, files):
        self.files = files
        self.path = files.data_dir / "agent-projects.sqlite3"

    @contextmanager
    def database(self):
        # Include schema creation, reconciliation and commits in the existing
        # workspace maintenance fence used by snapshots and relocation.
        with WorkspaceActivity(self.files.data_dir):
            with self._database() as db:
                yield db

    @contextmanager
    def _database(self):
        self.files.storage_mount.check()
        db = None
        try:
            if not self.files.data_dir.is_dir() or self.files.data_dir.is_symlink():
                fail("agent_projects_storage_unavailable", 503)
            # SQLite sidecars must not redirect writes outside workspace storage.
            for suffix in ("", "-journal", "-wal", "-shm"):
                path = self.path.with_name(self.path.name + suffix)
                if path.is_symlink() or (path.exists() and not path.is_file()):
                    fail("agent_projects_storage_unavailable", 503)
            descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            try:
                info = os.fstat(descriptor)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    fail("agent_projects_storage_unavailable", 503)
            finally:
                os.close(descriptor)
            db = sqlite3.connect(self.path, timeout=2, isolation_level=None)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("PRAGMA busy_timeout=2000")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                fail("agent_projects_schema_unsupported", 503)
            if version == 0:
                db.executescript("""
                    BEGIN IMMEDIATE;
                    CREATE TABLE IF NOT EXISTS agent_project_scopes (
                        tenant_id TEXT NOT NULL, owner_user_id TEXT NOT NULL,
                        revision INTEGER NOT NULL DEFAULT 0,
                        PRIMARY KEY (tenant_id, owner_user_id)
                    );
                    CREATE TABLE IF NOT EXISTS agent_projects (
                        tenant_id TEXT NOT NULL, owner_user_id TEXT NOT NULL,
                        id TEXT NOT NULL, name TEXT NOT NULL CHECK(length(trim(name)) > 0),
                        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                        PRIMARY KEY (tenant_id, owner_user_id, id)
                    );
                    CREATE TABLE IF NOT EXISTS agent_project_memberships (
                        tenant_id TEXT NOT NULL, owner_user_id TEXT NOT NULL,
                        agent_id TEXT NOT NULL CHECK(agent_id <> 'big-brother'),
                        project_id TEXT NOT NULL, updated_at TEXT NOT NULL,
                        PRIMARY KEY (tenant_id, owner_user_id, agent_id),
                        FOREIGN KEY (tenant_id, owner_user_id, project_id)
                            REFERENCES agent_projects(tenant_id, owner_user_id, id)
                            ON DELETE CASCADE
                    );
                    CREATE INDEX IF NOT EXISTS agent_project_memberships_project
                        ON agent_project_memberships(tenant_id, owner_user_id, project_id);
                    PRAGMA user_version=1;
                    COMMIT;
                """)
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except (OSError, sqlite3.Error):
            fail("agent_projects_storage_unavailable", 503)
        finally:
            if db is not None:
                db.close()  # Rolls back failed transactions.

    @staticmethod
    def snapshot(db, scope):
        row = db.execute(
            "SELECT revision FROM agent_project_scopes WHERE tenant_id=? AND owner_user_id=?",
            scope,
        ).fetchone()
        projects = db.execute(
            "SELECT id,name,created_at,updated_at FROM agent_projects "
            "WHERE tenant_id=? AND owner_user_id=? ORDER BY created_at,id",
            scope,
        ).fetchall()
        memberships = db.execute(
            "SELECT agent_id,project_id FROM agent_project_memberships "
            "WHERE tenant_id=? AND owner_user_id=?",
            scope,
        ).fetchall()
        return {
            "revision": row[0] if row else 0,
            "projects": [dict(project) for project in projects],
            "memberships": {item[0]: item[1] for item in memberships},
        }

    def read(self, scope):
        with self.database() as db:
            return self.snapshot(db, scope)

    def mutate(
        self, scope, expected_revision, operation, *, project_id=None, name=None, agent_id=None
    ):
        with self.database() as db:
            db.execute(
                "INSERT OR IGNORE INTO agent_project_scopes(tenant_id,owner_user_id) VALUES (?,?)",
                scope,
            )
            if self.snapshot(db, scope)["revision"] != expected_revision:
                fail("agent_projects_revision_conflict", 409)
            now = datetime.now(UTC).isoformat()
            if operation != "create" and project_id is not None:
                exists = db.execute(
                    "SELECT 1 FROM agent_projects WHERE tenant_id=? AND owner_user_id=? AND id=?",
                    (*scope, project_id),
                ).fetchone()
                if not exists:
                    fail("agent_project_not_found", 404)
            if operation == "create":
                db.execute(
                    "INSERT INTO agent_projects VALUES (?,?,?,?,?,?)",
                    (*scope, uuid.uuid4().hex, name, now, now),
                )
            elif operation == "rename":
                db.execute(
                    "UPDATE agent_projects SET name=?,updated_at=? "
                    "WHERE tenant_id=? AND owner_user_id=? AND id=?",
                    (name, now, *scope, project_id),
                )
            elif operation == "delete":
                db.execute(
                    "DELETE FROM agent_projects WHERE tenant_id=? AND owner_user_id=? AND id=?",
                    (*scope, project_id),
                )
            elif operation == "assign":
                if agent_id == "big-brother":
                    fail("protected_agent", 409)
                db.execute(
                    "DELETE FROM agent_project_memberships "
                    "WHERE tenant_id=? AND owner_user_id=? AND agent_id=?",
                    (*scope, agent_id),
                )
                if project_id is not None:
                    db.execute(
                        "INSERT INTO agent_project_memberships VALUES (?,?,?,?,?)",
                        (*scope, agent_id, project_id, now),
                    )
            else:
                raise ValueError("unsupported project operation")
            db.execute(
                "UPDATE agent_project_scopes SET revision=revision+1 "
                "WHERE tenant_id=? AND owner_user_id=?",
                scope,
            )
            return self.snapshot(db, scope)

    def forget_agent(self, agent_id):
        # No new storage on ordinary agent deletion before the feature is used.
        if not self.path.exists() and not self.path.is_symlink():
            return
        with self.database() as db:
            db.execute(
                "UPDATE agent_project_scopes SET revision=revision+1 WHERE EXISTS ("
                "SELECT 1 FROM agent_project_memberships m WHERE m.agent_id=? "
                "AND m.tenant_id=agent_project_scopes.tenant_id "
                "AND m.owner_user_id=agent_project_scopes.owner_user_id)",
                (agent_id,),
            )
            db.execute("DELETE FROM agent_project_memberships WHERE agent_id=?", (agent_id,))
