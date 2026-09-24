"""Durable workspace portability admission and worker claims.

The task table is the queue: accepting work never depends on a second broker
write. Filesystem publication is deliberately a separate, journalled concern.
"""

from __future__ import annotations

import errno
import hashlib
import hmac
import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .base import StoreError


class PortabilityTaskStore:
    """One authoritative database on the workspace's persistent local volume."""

    def __init__(self, data_dir: Path, *, capacity: int = 100):
        self.path = Path(data_dir) / "portability.sqlite3"
        self.capacity = capacity
        if self.path.is_symlink() or not self.path.parent.is_dir():
            raise StoreError("Task storage unavailable", status=503, code="task_store_unavailable")
        descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.close(descriptor)
        self.path.chmod(0o600)
        with self.transaction() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > 1:
                raise StoreError(
                    "Task storage requires a newer runtime",
                    status=503,
                    code="task_schema_incompatible",
                )
            schema = """
                CREATE TABLE IF NOT EXISTS portability_meta (
                    name TEXT PRIMARY KEY, value BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS portability_tasks (
                    id TEXT PRIMARY KEY, scope TEXT NOT NULL, actor TEXT NOT NULL,
                    kind TEXT NOT NULL CHECK(kind IN ('EXPORT','IMPORT')),
                    fingerprint TEXT NOT NULL, input_json TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN
                        ('PENDING','PROCESSING','COMPLETED','FAILED')),
                    revision INTEGER NOT NULL DEFAULT 1,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    started_at REAL, completed_at REAL,
                    available_at REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                    lease_owner TEXT, lease_until REAL,
                    fence INTEGER NOT NULL DEFAULT 0,
                    result_json TEXT, error_json TEXT
                );
                CREATE INDEX IF NOT EXISTS portability_queue
                    ON portability_tasks(status, available_at, created_at);
                CREATE TABLE IF NOT EXISTS portability_idempotency (
                    scope TEXT NOT NULL, actor TEXT NOT NULL, kind TEXT NOT NULL,
                    key_digest TEXT NOT NULL, task_id TEXT NOT NULL
                        REFERENCES portability_tasks(id),
                    PRIMARY KEY(scope, actor, kind, key_digest)
                );
                CREATE TABLE IF NOT EXISTS portability_pins (
                    upload_id TEXT PRIMARY KEY, task_id TEXT NOT NULL
                        REFERENCES portability_tasks(id)
                );
                CREATE TABLE IF NOT EXISTS portability_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL REFERENCES portability_tasks(id),
                    revision INTEGER NOT NULL, created_at REAL NOT NULL,
                    UNIQUE(task_id, revision)
                );
                CREATE TABLE IF NOT EXISTS portability_download_leases (
                    export_id TEXT PRIMARY KEY, expires_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS portability_import_journal (
                    task_id TEXT NOT NULL REFERENCES portability_tasks(id),
                    resource_kind TEXT NOT NULL, source_id TEXT NOT NULL,
                    target_id TEXT NOT NULL, digest TEXT, phase TEXT NOT NULL,
                    PRIMARY KEY(task_id, resource_kind, source_id),
                    UNIQUE(resource_kind, target_id)
                );
            """
            # executescript implicitly commits an open transaction. Execute the
            # fixed DDL individually so schema, version and HMAC secret either
            # commit together or roll back together after startup failure.
            for statement in schema.split(";"):
                if statement.strip():
                    db.execute(statement)
            db.execute("PRAGMA user_version=1")
            db.execute(
                "INSERT OR IGNORE INTO portability_meta(name,value) VALUES('fingerprint_key',?)",
                (os.urandom(32),),
            )
            self.secret = bytes(
                db.execute(
                    "SELECT value FROM portability_meta WHERE name='fingerprint_key'"
                ).fetchone()[0]
            )

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path, timeout=2, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except sqlite3.Error as error:
            db.rollback()
            raise StoreError(
                "Task storage unavailable", status=503, code="task_store_unavailable"
            ) from error
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def digest(self, value: Any) -> str:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hmac.new(self.secret, encoded.encode(), hashlib.sha256).hexdigest()

    @staticmethod
    def validate_key(key: str | None, *, required: bool) -> None:
        if key is None and not required:
            return
        if not key:
            raise StoreError("Idempotency-Key is required", code="idempotency_key_required")
        if len(key) > 128 or any(ord(char) < 33 or ord(char) > 126 for char in key):
            raise StoreError("Invalid Idempotency-Key", code="invalid_idempotency_key")

    def admit(
        self,
        *,
        scope: str,
        actor: str,
        kind: str,
        key: str | None,
        fingerprint: str,
        inputs: dict[str, Any],
        upload_id: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """Atomically deduplicate, pin, and enqueue; never persist raw secrets.

        Caller holds the transfer publication lock and verifies immutable upload
        availability before admission. Use replay() first when the input may have
        expired following a terminal request.
        """
        self.validate_key(key, required=kind == "IMPORT")
        if kind not in {"EXPORT", "IMPORT"} or not scope or not actor:
            raise ValueError("kind and trusted ownership scope are required")
        key_digest = self.digest(key) if key is not None else None
        now = time.time()
        with self.transaction() as db:
            prior = self._replay(db, scope, actor, kind, key_digest, fingerprint)
            if prior is not None:
                return dict(prior), False
            count = db.execute(
                "SELECT count(*) FROM portability_tasks WHERE status IN ('PENDING','PROCESSING')"
            ).fetchone()[0]
            if count >= self.capacity:
                raise StoreError("Task queue is full", status=429, code="task_queue_full")
            task_id = uuid.uuid4().hex
            db.execute(
                """INSERT INTO portability_tasks
                (id,scope,actor,kind,fingerprint,input_json,status,created_at,updated_at,available_at)
                VALUES(?,?,?,?,?,?,'PENDING',?,?,?)""",
                (task_id, scope, actor, kind, fingerprint, json.dumps(inputs), now, now, now),
            )
            if key_digest is not None:
                db.execute(
                    "INSERT INTO portability_idempotency VALUES(?,?,?,?,?)",
                    (scope, actor, kind, key_digest, task_id),
                )
            if upload_id:
                try:
                    db.execute("INSERT INTO portability_pins VALUES(?,?)", (upload_id, task_id))
                except sqlite3.IntegrityError as error:
                    raise StoreError(
                        "Upload is in use", status=409, code="transfer_in_use"
                    ) from error
            self._event(db, task_id, now)
            return dict(
                db.execute("SELECT * FROM portability_tasks WHERE id=?", (task_id,)).fetchone()
            ), True

    @staticmethod
    def _replay(db, scope, actor, kind, key_digest, fingerprint):
        if key_digest is None:
            return None
        row = db.execute(
            """SELECT t.* FROM portability_idempotency i
            JOIN portability_tasks t ON t.id=i.task_id
            WHERE i.scope=? AND i.actor=? AND i.kind=? AND i.key_digest=?""",
            (scope, actor, kind, key_digest),
        ).fetchone()
        if row is not None and not hmac.compare_digest(row["fingerprint"], fingerprint):
            raise StoreError(
                "Idempotency-Key conflicts with the original request",
                status=409,
                code="idempotency_conflict",
            )
        return row

    def replay(self, *, scope, actor, kind, key, fingerprint):
        self.validate_key(key, required=kind == "IMPORT")
        with self.transaction() as db:
            row = self._replay(
                db, scope, actor, kind, self.digest(key) if key is not None else None, fingerprint
            )
            return dict(row) if row is not None else None

    def get(self, task_id: str, *, scope: str, actor: str) -> dict[str, Any]:
        with self.transaction() as db:
            row = db.execute(
                "SELECT * FROM portability_tasks WHERE id=? AND scope=? AND actor=?",
                (task_id, scope, actor),
            ).fetchone()
            if row is None:
                raise StoreError("Task not found", status=404, code="task_not_found")
            return dict(row)

    def claim(self, owner: str, *, lease_seconds: float = 30) -> dict[str, Any] | None:
        # A takeover must not change the fence while the previous owner is in
        # its final filesystem publication section. Heartbeats remain independent
        # so a long operation can renew without waiting for this filesystem lock.
        with self.publication_lock():
            return self._claim_locked(owner, lease_seconds=lease_seconds)

    def _claim_locked(self, owner: str, *, lease_seconds: float) -> dict[str, Any] | None:
        now = time.time()
        with self.transaction() as db:
            if (
                db.execute(
                    "SELECT 1 FROM portability_tasks "
                    "WHERE status='PROCESSING' AND lease_until>? LIMIT 1",
                    (now,),
                ).fetchone()
                is not None
            ):
                return None
            row = db.execute(
                """SELECT * FROM portability_tasks
                WHERE (status='PENDING' AND available_at<=?)
                   OR (status='PROCESSING' AND lease_until<=?)
                ORDER BY created_at,id LIMIT 1""",
                (now, now),
            ).fetchone()
            if row is None:
                return None
            db.execute(
                """UPDATE portability_tasks SET status='PROCESSING',revision=revision+1,
                started_at=COALESCE(started_at,?),updated_at=?,lease_owner=?,lease_until=?,
                fence=fence+1,attempts=attempts+1 WHERE id=?""",
                (now, now, owner, now + lease_seconds, row["id"]),
            )
            self._event(db, row["id"], now)
            return dict(
                db.execute("SELECT * FROM portability_tasks WHERE id=?", (row["id"],)).fetchone()
            )

    def heartbeat(self, task_id: str, owner: str, fence: int, *, lease_seconds=30) -> bool:
        now = time.time()
        with self.transaction() as db:
            return (
                db.execute(
                    """UPDATE portability_tasks SET lease_until=? WHERE id=?
                AND status='PROCESSING' AND lease_owner=? AND fence=? AND lease_until>?""",
                    (now + lease_seconds, task_id, owner, fence, now),
                ).rowcount
                == 1
            )

    def finish(self, task_id, owner, fence, *, result=None, error=None) -> bool:
        if (result is None) == (error is None):
            raise ValueError("Exactly one terminal result or safe error is required")
        now = time.time()
        with self.transaction() as db:
            changed = db.execute(
                """UPDATE portability_tasks SET status=?,result_json=?,error_json=?,
                revision=revision+1,updated_at=?,completed_at=?,lease_owner=NULL,lease_until=NULL
                WHERE id=? AND status='PROCESSING' AND lease_owner=? AND fence=?
                AND lease_until>?""",
                (
                    "COMPLETED" if error is None else "FAILED",
                    json.dumps(result) if result is not None else None,
                    json.dumps(error) if error is not None else None,
                    now,
                    now,
                    task_id,
                    owner,
                    fence,
                    now,
                ),
            ).rowcount
            if changed:
                self._event(db, task_id, now)
                # Recovery-required failures retain their input pin for reconciliation.
                if error is None or error.get("code") != "import_recovery_required":
                    db.execute("DELETE FROM portability_pins WHERE task_id=?", (task_id,))
            return changed == 1

    @staticmethod
    def _event(db, task_id: str, now: float) -> None:
        db.execute(
            """INSERT INTO portability_events(task_id,revision,created_at)
            SELECT id,revision,? FROM portability_tasks WHERE id=?""",
            (now, task_id),
        )

    def list_tasks(self, *, scope: str, actor: str, before: str = "", limit: int = 50):
        """Stable descending creation pagination, scoped before cursor lookup."""
        if not 1 <= limit <= 100:
            raise StoreError("Invalid page size", code="invalid_task_cursor")
        with self.transaction() as db:
            cursor = None
            if before:
                cursor = db.execute(
                    "SELECT created_at,id FROM portability_tasks "
                    "WHERE id=? AND scope=? AND actor=?",
                    (before, scope, actor),
                ).fetchone()
                if cursor is None:
                    raise StoreError("Invalid task cursor", code="invalid_task_cursor")
            query = "SELECT * FROM portability_tasks WHERE scope=? AND actor=?"
            parameters = [scope, actor]
            if cursor is not None:
                query += " AND (created_at,id)<(?,?)"
                parameters.extend([cursor["created_at"], cursor["id"]])
            query += " ORDER BY created_at DESC,id DESC LIMIT ?"
            parameters.append(limit + 1)
            rows = db.execute(query, parameters).fetchall()
            return {
                "items": [dict(row) for row in rows[:limit]],
                "next_cursor": rows[limit - 1]["id"] if len(rows) > limit else None,
            }

    def events(self, *, scope: str, actor: str, after: int = 0, limit: int = 100):
        """Read notifications and current truth without leaking other scopes.

        Multiple transitions can project the same latest revision. Consumers must
        deduplicate by task revision, never infer a historical snapshot from this.
        """
        if after < 0 or not 1 <= limit <= 100:
            raise StoreError("Invalid event cursor", code="invalid_task_cursor")
        with self.transaction() as db:
            return [
                dict(row)
                for row in db.execute(
                    """SELECT t.*, e.id AS event_id FROM portability_events e
                JOIN portability_tasks t ON t.id=e.task_id
                WHERE t.scope=? AND t.actor=? AND e.id>?
                ORDER BY e.id LIMIT ?""",
                    (scope, actor, after, limit),
                ).fetchall()
            ]

    def pinned(self, upload_id: str) -> bool:
        with self.transaction() as db:
            return (
                db.execute(
                    "SELECT 1 FROM portability_pins WHERE upload_id=?", (upload_id,)
                ).fetchone()
                is not None
            )

    def owns_claim(self, task_id: str, owner: str, fence: int) -> bool:
        """Call under publication_lock immediately before filesystem effects."""
        with self.transaction() as db:
            return (
                db.execute(
                    """SELECT 1 FROM portability_tasks WHERE id=? AND status='PROCESSING'
                AND lease_owner=? AND fence=? AND lease_until>?""",
                    (task_id, owner, fence, time.time()),
                ).fetchone()
                is not None
            )

    @contextmanager
    def publication_lock(self):
        """Cross-process exclusion for admission pins and filesystem publication.

        Database leases alone cannot stop a paused worker from performing writes.
        All publishers and transfer deleters must participate in this lock and
        recheck their claim after acquisition. Never hold a DB transaction while
        waiting for this lock.
        """
        import fcntl

        path = self.path.parent / ".portability-publication.lock"
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            deadline = time.monotonic() + 0.1
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as error:
                    if error.errno not in {errno.EAGAIN, errno.EACCES}:
                        raise
                    if time.monotonic() >= deadline:
                        raise StoreError(
                            "Snapshot storage is busy", status=429, code="task_storage_busy"
                        ) from error
                    time.sleep(0.005)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def export_task(self, export_id: str, *, scope: str, actor: str):
        with self.transaction() as db:
            row = db.execute(
                """SELECT * FROM portability_tasks WHERE kind='EXPORT'
                AND scope=? AND actor=? AND json_extract(input_json,'$.export_id')=?""",
                (scope, actor, export_id),
            ).fetchone()
            if row is None:
                raise StoreError("Snapshot not found", status=404, code="snapshot_not_found")
            return dict(row)

    def reserve_target(self, task_id, owner, fence, *, kind, source_id, target_id):
        """Persist a stable mapping before any live filesystem mutation."""
        if kind not in {"PROFILE", "TEAM"}:
            raise ValueError("Invalid import resource kind")
        with self.transaction() as db:
            claim = db.execute(
                """SELECT 1 FROM portability_tasks WHERE id=? AND status='PROCESSING'
                AND lease_owner=? AND fence=? AND lease_until>?""",
                (task_id, owner, fence, time.time()),
            ).fetchone()
            if claim is None:
                raise StoreError("Import claim expired", status=409, code="task_claim_lost")
            prior = db.execute(
                """SELECT * FROM portability_import_journal
                WHERE task_id=? AND resource_kind=? AND source_id=?""",
                (task_id, kind, source_id),
            ).fetchone()
            if prior is not None:
                return dict(prior)
            try:
                db.execute(
                    """INSERT INTO portability_import_journal
                    (task_id,resource_kind,source_id,target_id,phase) VALUES(?,?,?,?,'RESERVED')""",
                    (task_id, kind, source_id, target_id),
                )
            except sqlite3.IntegrityError as error:
                raise StoreError(
                    "Import target is reserved", status=409, code="target_reserved"
                ) from error
            return dict(
                db.execute(
                    """SELECT * FROM portability_import_journal
                WHERE task_id=? AND resource_kind=? AND source_id=?""",
                    (task_id, kind, source_id),
                ).fetchone()
            )

    def journal(self, task_id):
        with self.transaction() as db:
            return [
                dict(row)
                for row in db.execute(
                    """SELECT * FROM portability_import_journal WHERE task_id=?
                ORDER BY resource_kind,source_id""",
                    (task_id,),
                ).fetchall()
            ]

    def record_publication(self, task_id, owner, fence, *, kind, source_id, digest, phase):
        """Record intent before rename, then receipt after directory fsync."""
        if phase not in {"PREPARED", "PUBLISHED"}:
            raise ValueError("Invalid import journal transition")
        with self.transaction() as db:
            claim = db.execute(
                """SELECT 1 FROM portability_tasks WHERE id=? AND status='PROCESSING'
                AND lease_owner=? AND fence=? AND lease_until>?""",
                (task_id, owner, fence, time.time()),
            ).fetchone()
            if claim is None:
                raise StoreError("Import claim expired", status=409, code="task_claim_lost")
            row = db.execute(
                """SELECT digest,phase FROM portability_import_journal
                WHERE task_id=? AND resource_kind=? AND source_id=?""",
                (task_id, kind, source_id),
            ).fetchone()
            if row is None or (row["digest"] is not None and row["digest"] != digest):
                raise StoreError(
                    "Import journal conflict", status=409, code="import_recovery_required"
                )
            if phase == "PUBLISHED" and row["phase"] not in {"PREPARED", "PUBLISHED"}:
                raise StoreError(
                    "Missing publication intent", status=409, code="import_recovery_required"
                )
            if row["phase"] == "PUBLISHED":
                return
            db.execute(
                """UPDATE portability_import_journal SET digest=?,phase=?
                WHERE task_id=? AND resource_kind=? AND source_id=?""",
                (digest, phase, task_id, kind, source_id),
            )

    def resource_visible(self, kind: str, target_id: str) -> bool:
        """One logical commit controls visibility of every imported resource."""
        with self.transaction() as db:
            row = db.execute(
                """SELECT t.status FROM portability_import_journal j
                JOIN portability_tasks t ON t.id=j.task_id
                WHERE j.resource_kind=? AND j.target_id=?""",
                (kind, target_id),
            ).fetchone()
            return row is None or row["status"] == "COMPLETED"

    def retry_claim(self, task_id, owner, fence, *, delay_seconds=5, max_attempts=3):
        """Retry transient failures only before any resource publication intent."""
        now = time.time()
        with self.transaction() as db:
            changed = db.execute(
                """UPDATE portability_tasks SET status='PENDING',revision=revision+1,
                updated_at=?,available_at=?,lease_owner=NULL,lease_until=NULL
                WHERE id=? AND status='PROCESSING' AND lease_owner=? AND fence=?
                AND lease_until>? AND attempts<? AND NOT EXISTS (
                    SELECT 1 FROM portability_import_journal j WHERE j.task_id=portability_tasks.id
                    AND j.phase IN ('PREPARED','PUBLISHED'))""",
                (now, now + delay_seconds, task_id, owner, fence, now, max_attempts),
            ).rowcount
            if changed:
                self._event(db, task_id, now)
            return changed == 1

    @contextmanager
    def preparation_lock(self, task_id: str):
        """Serialize one task's private stage without blocking other admissions."""
        import fcntl

        if not task_id or any(char not in "0123456789abcdef" for char in task_id):
            raise ValueError("Invalid task identity")
        root = self.path.parent / "portability-locks"
        root.mkdir(mode=0o700, exist_ok=True)
        if root.is_symlink():
            raise StoreError("Task storage unavailable", status=503, code="task_store_unavailable")
        descriptor = os.open(root / task_id, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def artifact_reader(self, export_id, *, renew=False):
        """Persist short reader grace across chunk requests and process restarts."""
        with self.transaction() as db:
            now = time.time()
            if renew:
                db.execute(
                    """INSERT INTO portability_download_leases VALUES(?,?)
                    ON CONFLICT(export_id) DO UPDATE SET expires_at=excluded.expires_at""",
                    (export_id, now + 120),
                )
                return True
            row = db.execute(
                "SELECT expires_at FROM portability_download_leases WHERE export_id=?",
                (export_id,),
            ).fetchone()
            return row is not None and row["expires_at"] > now
