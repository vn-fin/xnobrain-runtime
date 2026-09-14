"""Durable app schedule authority; native cron alone calculates recurrence."""

from __future__ import annotations

import json
import os
import uuid

from ..models.custom_page import canonical, digest
from .base import StoreError

_PROCESS = uuid.uuid4().hex


def fail(code, status=409):
    raise StoreError(code.replace("_", " "), status=status, code=code)


class CustomPageScheduleRepository:
    def __init__(self, pages):
        self.pages = pages

    @staticmethod
    def ensure(db):
        db.execute("""CREATE TABLE IF NOT EXISTS schedules (
            id TEXT PRIMARY KEY, approval TEXT NOT NULL, digest TEXT NOT NULL,
            receipt_key TEXT NOT NULL UNIQUE, state TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0, occurrence TEXT, run_id TEXT,
            last_status TEXT, updated_at TEXT NOT NULL)""")

        existing = {row[1] for row in db.execute("PRAGMA table_info(schedules)")}
        for name, kind in (
            ("worker_pid", "INTEGER"),
            ("worker_started", "INTEGER"),
            ("worker_process", "TEXT"),
        ):
            if name not in existing:
                db.execute(f"ALTER TABLE schedules ADD COLUMN {name} {kind}")

    @staticmethod
    def worker_live(binding):
        if binding.get("worker_process") == _PROCESS:
            return False  # Caller additionally checks its in-process executing set.
        pid = binding.get("worker_pid")
        if not pid:
            return True  # Legacy/unknown owner is not proof of quiescence.
        from gateway.status import get_process_start_time

        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        current = get_process_start_time(pid)
        started = binding.get("worker_started")
        return current is None or started is None or current == started

    @staticmethod
    def present(row):
        return {**dict(row), "approval": json.loads(row["approval"])}

    def list(self, agent, owner):
        with self.pages.database(agent, owner) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='schedules'").fetchone():
                return []
            return [
                self.present(row)
                for row in db.execute("SELECT * FROM schedules ORDER BY updated_at DESC")
            ]

    def get(self, agent, owner, identifier):
        row = next((row for row in self.list(agent, owner) if row["id"] == identifier), None)
        if row is None:
            fail("custom_page_schedule_not_found", 404)
        return row

    def create(self, agent, owner, approval, key, checksum):
        with self.pages.database(agent, owner, write=True) as db:
            self.ensure(db)
            state = self.pages.state(db)
            if state["status"] != "active" or state["active"] != approval["expected_revision"]:
                fail("custom_page_revision_conflict")
            existing = db.execute("SELECT * FROM schedules WHERE receipt_key=?", (key,)).fetchone()
            if existing:
                if existing["digest"] != checksum:
                    fail("custom_page_idempotency_conflict")
                return self.present(existing)
            if db.execute("SELECT count(*) FROM schedules").fetchone()[0] >= 100:
                fail("custom_page_schedule_limit", 413)
            identifier = "xcp_" + uuid.uuid4().hex
            db.execute(
                "INSERT INTO schedules(id,approval,digest,receipt_key,state,updated_at) VALUES (?,?,?,?,'preparing',?)",
                (identifier, canonical(approval), checksum, key, self.pages.now()),
            )
            self.pages.quota(db)
            self.pages.log(
                db,
                "schedule_approved",
                {"schedule_id": identifier, "revision": approval["expected_revision"]},
            )
            return self.present(
                db.execute("SELECT * FROM schedules WHERE id=?", (identifier,)).fetchone()
            )

    def installed(self, agent, owner, identifier):
        with self.pages.database(agent, owner, write=True) as db:
            db.execute(
                "UPDATE schedules SET state='scheduled',updated_at=? WHERE id=? AND state='preparing'",
                (self.pages.now(), identifier),
            )

    def claim(self, agent, owner, identifier, occurrence):
        """Consume an occurrence before execution. Unknown claims never replay."""
        with self.pages.database(agent, owner, write=True) as db:
            self.ensure(db)
            row = db.execute("SELECT * FROM schedules WHERE id=?", (identifier,)).fetchone()
            if row is None:
                fail("custom_page_schedule_not_found", 404)
            binding = self.present(row)
            app = self.pages.state(db)
            if (
                binding["state"] != "scheduled"
                or app["status"] != "active"
                or app["active"] != binding["approval"]["expected_revision"]
                or binding["attempts"] >= binding["approval"]["max_runs"]
                or binding["occurrence"] == occurrence
            ):
                fail("custom_page_schedule_inactive")
            db.execute(
                "UPDATE schedules SET state='running',attempts=attempts+1,occurrence=?,run_id=NULL,last_status='dispatching',updated_at=? WHERE id=?",
                (occurrence, self.pages.now(), identifier),
            )
            from gateway.status import get_process_start_time

            db.execute(
                "UPDATE schedules SET worker_pid=?,worker_started=?,worker_process=? WHERE id=?",
                (os.getpid(), get_process_start_time(os.getpid()), _PROCESS, identifier),
            )
            self.pages.log(
                db, "schedule_claimed", {"schedule_id": identifier, "occurrence": occurrence}
            )
            return {
                **binding,
                "attempts": binding["attempts"] + 1,
                "occurrence": occurrence,
                "idempotency_key": "schedule:"
                + digest({"id": identifier, "occurrence": occurrence})[7:],
            }

    def attach(self, agent, owner, identifier, run_id):
        with self.pages.database(agent, owner, write=True) as db:
            cursor = db.execute(
                "UPDATE schedules SET run_id=?,last_status='running',updated_at=? WHERE id=? AND state='running'",
                (run_id, self.pages.now(), identifier),
            )
            if cursor.rowcount != 1:
                fail("custom_page_schedule_inactive")

    def finish(self, agent, owner, identifier, status):
        with self.pages.database(agent, owner, write=True) as db:
            row = db.execute("SELECT * FROM schedules WHERE id=?", (identifier,)).fetchone()
            if row is None:
                return
            binding = self.present(row)
            # Any failure stops future spend until separately approved again.
            state = (
                "scheduled"
                if status == "completed" and binding["attempts"] < binding["approval"]["max_runs"]
                else "stopped"
            )
            if binding["state"] in {"stopping", "stopped"}:
                state = "stopped"
            if status == "unknown":
                state = "stopping"
            db.execute(
                "UPDATE schedules SET state=?,last_status=?,updated_at=? WHERE id=?",
                (state, status, self.pages.now(), identifier),
            )
            # Stop/retry preserves the last result; it is not another completed
            # occurrence and must not advance the UI's last-completion time.
            if binding["last_status"] != status:
                self.pages.log(
                    db,
                    "schedule_finished",
                    {"schedule_id": identifier, "run_id": binding["run_id"], "status": status},
                )

    def stop(self, agent, owner, identifier):
        self.get(agent, owner, identifier)
        with self.pages.database(agent, owner, write=True) as db:
            row = db.execute("SELECT * FROM schedules WHERE id=?", (identifier,)).fetchone()
            if row is None:
                fail("custom_page_schedule_not_found", 404)
            state = "stopping" if row["state"] in {"running", "stopping"} else "stopped"
            db.execute(
                "UPDATE schedules SET state=?,updated_at=? WHERE id=?",
                (state, self.pages.now(), identifier),
            )
            return self.present(row)

    def deny(self, agent, owner, identifier):
        with self.pages.database(agent, owner, write=True) as db:
            db.execute(
                "UPDATE schedules SET state='stopped',last_status='failed',updated_at=? WHERE id=? AND state IN ('scheduled','preparing')",
                (self.pages.now(), identifier),
            )

    @staticmethod
    def quiescent(db, *, archive=False):
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='schedules'").fetchone():
            return
        if db.execute("SELECT 1 FROM schedules WHERE state IN ('running','stopping')").fetchone():
            fail("custom_page_jobs_active")
        if archive:
            db.execute("UPDATE schedules SET state='stopped' WHERE state!='stopped'")
