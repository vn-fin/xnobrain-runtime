"""Private durable execution input and dispatch journal; never a public DTO."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from contextlib import contextmanager

from .base import StoreError
from .custom_page_locks import acquire, release


class AdmissionRepository:
    def team_fence(self, identifier):
        name = ".capacity-team-" + hashlib.sha256(identifier.encode()).hexdigest() + ".lock"
        return acquire(self.root, name, shared=False)

    def team_active(self, identifier):
        try:
            descriptor = self.team_fence(identifier)
        except StoreError:
            return True
        release(descriptor)
        return False

    def __init__(self, root):
        self.root = root
        self.path = root / "agent-run-admission.sqlite"
        root.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS native_roots (id TEXT PRIMARY KEY, kind TEXT NOT NULL, state TEXT NOT NULL, admission TEXT NOT NULL, updated REAL NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS native_sources (id TEXT PRIMARY KEY, source_key TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS pending_runs ("
                "id TEXT PRIMARY KEY, agent TEXT NOT NULL, conversation TEXT NOT NULL, "
                "state TEXT NOT NULL, payload TEXT NOT NULL, admission TEXT NOT NULL, "
                "created REAL NOT NULL)"
            )

    @contextmanager
    def connection(self):
        if self.path.is_symlink():
            raise StoreError(
                "Unsafe admission storage", code="capacity_storage_unavailable", status=503
            )
        descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.close(descriptor)
        db = sqlite3.connect(self.path, timeout=2)
        try:
            with db:
                db.row_factory = sqlite3.Row
                db.execute("PRAGMA busy_timeout=2000")
                yield db
        finally:
            db.close()

    def enqueue(self, record, payload):
        encoded = json.dumps(payload, ensure_ascii=False)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT id FROM pending_runs WHERE id=?", (record["id"],)
            ).fetchone()
            if existing:
                return
            count = db.execute(
                "SELECT (SELECT count(*) FROM pending_runs WHERE state IN ('prepared','waiting','claiming'))"
                " + (SELECT count(*) FROM native_roots WHERE state IN ('waiting','claiming'))"
            ).fetchone()[0]
            if count >= 100:
                raise StoreError("Task queue is full", status=429, code="capacity_queue_full")
            try:
                db.execute(
                    "INSERT INTO pending_runs VALUES(?,?,?,'prepared',?,'{}',?)",
                    (
                        record["id"],
                        record["agent_id"],
                        record["conversation_id"],
                        encoded,
                        time.time(),
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise StoreError(
                    "Conversation already has a queued task",
                    status=409,
                    code="conversation_running",
                ) from error

    def pending(self):
        with self.connection() as db:
            rows = db.execute("SELECT * FROM pending_runs ORDER BY created,id").fetchall()
        return [
            dict(row)
            | {"payload": json.loads(row["payload"]), "admission": json.loads(row["admission"])}
            for row in rows
        ]

    def update(self, identifier, state, admission):
        with self.connection() as db:
            db.execute(
                "UPDATE pending_runs SET state=?,admission=? WHERE id=?",
                (state, json.dumps(admission), identifier),
            )

    def remove(self, identifier):
        with self.connection() as db:
            db.execute("DELETE FROM pending_runs WHERE id=?", (identifier,))

    def native_get(self, identifier):
        with self.connection() as db:
            row = db.execute("SELECT * FROM native_roots WHERE id=?", (identifier,)).fetchone()
        return dict(row) | {"admission": json.loads(row["admission"])} if row else None

    def native_put(self, identifier, kind, state, admission, source_key=""):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM native_roots WHERE id=?", (identifier,)).fetchone():
                count = db.execute(
                    "SELECT (SELECT count(*) FROM pending_runs WHERE state IN ('prepared','waiting','claiming'))"
                    " + (SELECT count(*) FROM native_roots WHERE state IN ('waiting','claiming'))"
                ).fetchone()[0]
                if count >= 100:
                    raise StoreError("Task queue is full", status=429, code="capacity_queue_full")
            db.execute(
                "INSERT INTO native_roots VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET state=excluded.state,admission=excluded.admission,updated=excluded.updated",
                (identifier, kind, state, json.dumps(admission), time.time()),
            )
            if source_key:
                db.execute(
                    "INSERT OR IGNORE INTO native_sources VALUES(?,?)", (identifier, source_key)
                )

    def native_source(self, identifier):
        with self.connection() as db:
            row = db.execute(
                "SELECT source_key FROM native_sources WHERE id=?", (identifier,)
            ).fetchone()
        return row[0] if row else ""

    def native_pending(self):
        with self.connection() as db:
            return [
                row[0]
                for row in db.execute(
                    "SELECT id FROM native_roots WHERE state IN ('waiting','claiming','starting','running','finished','cancelled') ORDER BY updated LIMIT 200"
                )
            ]

    def contains(self, identifier):
        with self.connection() as db:
            return (
                db.execute("SELECT 1 FROM pending_runs WHERE id=?", (identifier,)).fetchone()
                is not None
            )

    def is_waiting(self, identifier):
        with self.connection() as db:
            return (
                db.execute(
                    "SELECT 1 FROM pending_runs WHERE id=? AND state IN ('prepared','waiting','claiming')",
                    (identifier,),
                ).fetchone()
                is not None
            )
