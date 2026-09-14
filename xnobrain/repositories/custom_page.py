"""Dedicated app SQLite persistence; no Hermes database or external SQL access."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import UTC
from pathlib import Path

from ..models.custom_page import PageManifest, canonical, digest
from .base import StoreError
from .custom_page_locks import acquire, lifecycle_gate, release
from .custom_page_schedules import CustomPageScheduleRepository
from .runtime_update_gate import WorkspaceActivity

MAX_BYTES = 64 * 1024 * 1024
MAX_RECORDS = 10000


def fail(code, status=409):
    raise StoreError(code.replace("_", " "), status=status, code=code)


class CustomPageRepository:
    def __init__(self, files):
        self.files = files
        self.root = files.data_dir / "agent-apps"

    def directory(self, agent_id, *, create=False):
        name = self.files._id(agent_id, "agent_id")
        if not self.files.data_dir.is_dir() or self.files.data_dir.is_symlink():
            fail("custom_page_storage_unavailable", 503)
        self.files.storage_mount.check()
        path = self.root / name
        for parent in (self.root, path):
            if parent.is_symlink():
                fail("custom_page_unsafe_storage", 409)
            if create:
                try:
                    parent.mkdir(exist_ok=True, mode=0o700)
                except OSError:
                    fail("custom_page_storage_unavailable", 503)
        for leaf in (
            "app.sqlite3",
            "app.sqlite3-wal",
            "app.sqlite3-shm",
            "app.sqlite3-journal",
            "backup.sqlite3",
        ):
            if (path / leaf).is_symlink():
                fail("custom_page_unsafe_storage", 409)
        return path

    @contextmanager
    def lock(self, *, mutation=False):
        self.files.storage_mount.check()
        with WorkspaceActivity(self.files.data_dir, mutation=mutation):
            if not self.files._lock.acquire(timeout=2):
                fail("custom_page_storage_busy", 503)
            descriptor = None
            try:
                local = getattr(self.files, "_custom_page_lock_local", None)
                if local is None:
                    local = self.files._custom_page_lock_local = threading.local()
                if getattr(local, "pid", None) != os.getpid():
                    descriptor = acquire(
                        self.files.data_dir, ".custom-page-storage.lock", shared=False, timeout=2
                    )
                    local.pid = os.getpid()
                yield
            finally:
                if descriptor is not None:
                    local.pid = None
                    release(descriptor)
                self.files._lock.release()

    @contextmanager
    def database(self, agent_id, owner, *, create=False, write=False):
        with self.lock(mutation=create or write):
            if create:
                # Recheck profile presence under the same lock as agent removal.
                raw_profile = self.files.live_profile_path(agent_id)
                if raw_profile.is_symlink() or not self.files.live_profile_path(agent_id).is_dir():
                    fail("agent_not_found", 404)
            path = self.directory(agent_id, create=create) / "app.sqlite3"
            if not path.is_file() and not create:
                fail("custom_page_not_found", 404)
            connection = None
            try:
                connection = sqlite3.connect(
                    path.as_uri() + "?mode=" + ("rwc" if create else "rw" if write else "ro"),
                    uri=True,
                    timeout=2,
                )
                if create or write:
                    os.chmod(path, 0o600)
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA foreign_keys=ON")
                if create or write:
                    connection.execute("PRAGMA max_page_count=16384")
                connection.enable_load_extension(False)
                if create:
                    connection.executescript("""
                    CREATE TABLE IF NOT EXISTS app (
                        singleton INTEGER PRIMARY KEY, owner TEXT NOT NULL, active INTEGER NOT NULL,
                        status TEXT NOT NULL, updated_at TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS revisions (
                        revision INTEGER PRIMARY KEY, manifest TEXT NOT NULL, digest TEXT NOT NULL,
                        base_revision INTEGER NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS records (
                        dataset TEXT NOT NULL, id TEXT NOT NULL, value TEXT NOT NULL,
                        provenance TEXT NOT NULL, updated_at TEXT NOT NULL,
                        PRIMARY KEY(dataset,id));
                    CREATE TABLE IF NOT EXISTS receipts (
                        key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, result TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS activity (
                        id INTEGER PRIMARY KEY, kind TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS attachments (
                        id TEXT PRIMARY KEY, filename TEXT NOT NULL, digest TEXT NOT NULL, content BLOB NOT NULL);
                    """)
                connection.execute("BEGIN IMMEDIATE" if write or create else "BEGIN")
                if create:
                    connection.execute(
                        "INSERT OR IGNORE INTO app VALUES (1,?,0,'draft',?)", (owner, self.now())
                    )
                state = connection.execute("SELECT owner FROM app WHERE singleton=1").fetchone()
                if state is None or state["owner"] != owner:
                    fail("custom_page_not_found", 404)
                yield connection
                connection.commit()
            except OSError:
                fail("custom_page_storage_unavailable", 503)
            except sqlite3.Error as error:
                if connection:
                    connection.rollback()
                if "interrupted" in str(error).lower():
                    fail("custom_page_query_timeout", 503)
                code = (
                    "custom_page_storage_busy"
                    if "locked" in str(error).lower()
                    else "custom_page_storage_unavailable"
                )
                fail(code, 503)
            finally:
                if connection:
                    connection.close()

    @staticmethod
    def now():
        from datetime import datetime

        return datetime.now(UTC).isoformat()

    @staticmethod
    def state(db):
        return dict(
            db.execute("SELECT active,status,updated_at FROM app WHERE singleton=1").fetchone()
        )

    @staticmethod
    def revision(db, number):
        row = db.execute("SELECT * FROM revisions WHERE revision=?", (number,)).fetchone()
        if row is None:
            fail("custom_page_revision_not_found", 404)
        result = dict(row)
        try:
            manifest = json.loads(result["manifest"])
            parsed = PageManifest.model_validate(manifest).model_dump()
            if digest(parsed) != result["digest"]:
                fail("custom_page_manifest_corrupt", 409)
            result["manifest"] = parsed
        except ValueError:
            fail("custom_page_manifest_unsupported", 409)
        return result

    @staticmethod
    def replay(db, key, fingerprint):
        row = db.execute("SELECT fingerprint,result FROM receipts WHERE key=?", (key,)).fetchone()
        if row is not None:
            if row["fingerprint"] != fingerprint:
                fail("custom_page_idempotency_conflict")
            return json.loads(row["result"])
        return None

    @staticmethod
    def receipt(db, key, fingerprint, result):
        if db.execute("SELECT count(*) FROM receipts").fetchone()[0] >= 10000:
            fail("custom_page_receipt_quota_exceeded", 413)
        db.execute("INSERT INTO receipts VALUES (?,?,?)", (key, fingerprint, canonical(result)))

    def log(self, db, kind, detail):
        db.execute(
            "INSERT INTO activity(kind,detail,created_at) VALUES (?,?,?)",
            (kind, canonical(detail), self.now()),
        )
        db.execute(
            "DELETE FROM activity WHERE id NOT IN (SELECT id FROM activity ORDER BY id DESC LIMIT 1000)"
        )
        db.execute("UPDATE app SET updated_at=?", (self.now(),))

    def read(self, agent_id, owner):
        with self.database(agent_id, owner) as db:
            state = self.state(db)
            state["page"] = self.revision(db, state["active"]) if state["active"] else None
            state["revisions"] = [
                dict(r)
                for r in db.execute(
                    "SELECT revision,digest,base_revision,status,created_at FROM revisions ORDER BY revision DESC LIMIT 50"
                )
            ]
            return state

    def remove_agent(self, agent_id, owner, expected, remove_profile):
        """Journal before deleting a profile; never delete a replacement on replay."""
        if agent_id == "big-brother":
            fail("protected_agent", 409)
        with lifecycle_gate(self.files.data_dir, agent_id), self.lock(mutation=True):
            with self.database(agent_id, owner, write=True) as db:
                state = self.state(db)
                if state["status"] != "archived" or state["active"] != expected:
                    fail("custom_page_archive_required")
                CustomPageScheduleRepository.quiescent(db)
                from .custom_page_removal import initialize, journal

                receipt = journal(db)
                if receipt and receipt["state"] == "complete":
                    if self.files.live_profile_path(agent_id).exists():
                        fail("custom_page_agent_replaced")
                    return {"deleted": True, "app_retained": True, "active_revision": expected}
                if receipt:
                    fail("custom_page_agent_removal_interrupted")
                initialize(db, self.files.live_profile_path(agent_id), expected)
            if self.files.live_profile_path(agent_id).is_dir():
                remove_profile()
            with self.database(agent_id, owner, write=True) as db:
                db.execute("UPDATE agent_removal SET state='complete' WHERE singleton=1")
                self.log(db, "agent_removed", {"revision": expected, "app_retained": True})
            return {"deleted": True, "app_retained": True, "active_revision": expected}

    def removal_recovery(self, agent_id, owner):
        from .custom_page_removal import plan

        with self.database(agent_id, owner) as db:
            return plan(db, self.files.live_profile_path(agent_id), agent_id)

    def recover_removal(self, agent_id, owner, selected, remove_profile, finalize):
        from .custom_page_removal import journal, plan

        if agent_id == "big-brother":
            fail("protected_agent", 409)
        with lifecycle_gate(self.files.data_dir, agent_id), self.lock(mutation=True):
            profile = self.files.live_profile_path(agent_id)
            with self.database(agent_id, owner, write=True) as db:
                current = plan(db, profile, agent_id)
                receipt = journal(db)
                if (
                    current["operation_id"] != selected["operation_id"]
                    or current["expected_revision"] != selected["expected_revision"]
                ):
                    fail("custom_page_revision_conflict")
                if (
                    current["state"] == "complete"
                    and receipt.get("recovery_digest") == selected["digest"]
                ):
                    return {
                        "deleted": True,
                        "app_retained": True,
                        "active_revision": current["expected_revision"],
                    }
                if current["state"] != "recoverable" or current["digest"] != selected["digest"]:
                    fail("custom_page_agent_removal_plan_changed")
                CustomPageScheduleRepository.quiescent(db)
                db.execute(
                    "UPDATE agent_removal SET recovery_digest=? WHERE singleton=1",
                    (selected["digest"],),
                )
            if current["next_action"] == "remove_original":
                remove_profile()
            # Profile deletion can succeed before registry/cache finalization fails.
            finalize()
            with self.database(agent_id, owner, write=True) as db:
                if profile.exists():
                    fail("custom_page_agent_replaced")
                db.execute("UPDATE agent_removal SET state='complete' WHERE singleton=1")
                self.log(
                    db,
                    "agent_removed",
                    {
                        "revision": current["expected_revision"],
                        "app_retained": True,
                        "recovered": True,
                    },
                )
            return {
                "deleted": True,
                "app_retained": True,
                "active_revision": current["expected_revision"],
            }

    def retained(self, owner, offset, limit):
        # Bounded local catalog; no manifests/foreign names leave this boundary.
        self.directory("retained-storage-check")
        if not self.root.exists():
            return {"items": [], "next_offset": None}
        found = []
        deadline = time.monotonic() + 2
        with self.lock(), os.scandir(self.root) as entries:
            for index, entry in enumerate(entries):
                if index >= 1000 or time.monotonic() > deadline:
                    fail("custom_page_catalog_limit", 503)
                if entry.name.startswith("deleted-"):
                    continue
                if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                    continue
                if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", entry.name):
                    continue
                if self.files.live_profile_path(entry.name).exists():
                    continue
                try:
                    with self.database(entry.name, owner) as db:
                        state = self.state(db)
                        if state["status"] != "archived":
                            continue
                        row = db.execute(
                            "SELECT manifest FROM revisions ORDER BY revision DESC LIMIT 1"
                        ).fetchone()
                        try:
                            title = (
                                PageManifest.model_validate_json(row[0]).title
                                if row
                                else entry.name
                            )
                        except ValueError:
                            fail("custom_page_manifest_unsupported", 409)
                        found.append(
                            {
                                "agent_id": entry.name,
                                "title": title,
                                "active_revision": state["active"],
                                "updated_at": state["updated_at"],
                            }
                        )
                except StoreError as error:
                    if error.status != 404:
                        raise
        found.sort(key=lambda item: item["agent_id"])
        return {
            "items": found[offset : offset + limit],
            "next_offset": offset + limit if offset + limit < len(found) else None,
        }

    def prepare(self, agent_id, owner, body):
        fingerprint = digest(body)
        key = "prepare:" + body["idempotency_key"]
        with self.database(agent_id, owner, create=True, write=True) as db:
            replay = self.replay(db, key, fingerprint)
            if replay is not None:
                return replay
            state = self.state(db)
            if state["status"] == "archived":
                fail("custom_page_archived")
            if state["active"] != body["expected_revision"]:
                fail("custom_page_revision_conflict")
            number = db.execute("SELECT COALESCE(MAX(revision),0)+1 FROM revisions").fetchone()[0]
            manifest = body["manifest"]
            db.execute(
                "INSERT INTO revisions VALUES (?,?,?,?,?,?)",
                (
                    number,
                    canonical(manifest),
                    digest(manifest),
                    state["active"],
                    "preview_ready",
                    self.now(),
                ),
            )
            result = self.revision(db, number)
            # Bound abandoned drafts as well as activated history, protecting the
            # active and latest revision. Content-free receipts retain dedupe.
            db.execute(
                "DELETE FROM revisions WHERE revision NOT IN (SELECT revision FROM revisions ORDER BY revision DESC LIMIT 50) AND revision<>(SELECT active FROM app)"
            )
            self.receipt(db, key, fingerprint, result)
            self.log(db, "draft_prepared", {"revision": number})
            self.quota(db)
            return result

    def preview(self, agent_id, owner, number):
        with self.database(agent_id, owner) as db:
            return self.revision(db, number)

    def activate(self, agent_id, owner, body, compatible, validate_records, *, restore=False):
        with self.database(agent_id, owner, write=True) as db:
            state = self.state(db)
            target = self.revision(db, body["revision"])
            if target["digest"] != body["digest"]:
                fail("custom_page_digest_conflict")
            receipt_key = ("restore:" if restore else "activate:") + digest(body)
            replay = self.replay(db, receipt_key, digest(body))
            if replay is not None:
                return replay
            if state["active"] != body["expected_revision"]:
                fail("custom_page_revision_conflict")
            if state["status"] == "archived":
                fail("custom_page_archived")
            if target["status"] == "cancelled":
                fail("custom_page_draft_cancelled")
            if state["active"]:
                active = self.revision(db, state["active"])
                compatible(active["manifest"], target["manifest"])
            if not restore and target["base_revision"] != state["active"]:
                fail("custom_page_revision_conflict")
            if restore and not state["active"]:
                fail("custom_page_not_active")
            if restore:
                # Restore presentation against the current schema, never replace
                # new datasets/fields with their historical declarations.
                target["manifest"]["datasets"] = active["manifest"]["datasets"]
                target["digest"] = digest(target["manifest"])
                number = db.execute("SELECT MAX(revision)+1 FROM revisions").fetchone()[0]
                db.execute(
                    "INSERT INTO revisions VALUES (?,?,?,?,?,?)",
                    (
                        number,
                        canonical(target["manifest"]),
                        target["digest"],
                        state["active"],
                        "activated",
                        self.now(),
                    ),
                )
                target = self.revision(db, number)
            # Initial draft ingestion may precede activation. Never advertise a
            # schema that cannot read all existing rows, even if another draft
            # was used for collection. This validation shares the pointer transaction.
            for stored in db.execute("SELECT dataset,id,value,provenance FROM records"):
                validate_records(
                    target["manifest"],
                    stored["dataset"],
                    [
                        {
                            "id": stored["id"],
                            "values": json.loads(stored["value"]),
                            "provenance": json.loads(stored["provenance"]),
                        }
                    ],
                )
            db.execute(
                "UPDATE app SET active=?,status='active',updated_at=?",
                (target["revision"], self.now()),
            )
            db.execute(
                "UPDATE revisions SET status='activated' WHERE revision=?", (target["revision"],)
            )
            self.log(
                db,
                "page_restored" if restore else "page_activated",
                {"revision": target["revision"]},
            )
            result = self.revision(db, target["revision"])
            self.receipt(db, receipt_key, digest(body), result)
            return result

    def write_records(self, agent_id, owner, dataset, body, validate):
        key = "write:" + body["idempotency_key"]
        fingerprint = digest({"dataset": dataset, **body})
        with self.database(agent_id, owner, write=True) as db:
            state = self.state(db)
            if state["status"] == "archived":
                fail("custom_page_archived")
            result = self.replay(db, key, fingerprint)
            if result is not None:
                return result
            revision = state["active"] or body["expected_revision"]
            if revision != body["expected_revision"]:
                fail("custom_page_revision_conflict")
            candidate = self.revision(db, revision)
            if not state["active"] and candidate["status"] != "preview_ready":
                fail("custom_page_revision_conflict")
            manifest = candidate["manifest"]
            validate(manifest, dataset, body["records"])
            for row in body["records"]:
                # References are checked in the same transaction as the write.
                fields = next(d["fields"] for d in manifest["datasets"] if d["id"] == dataset)
                for field in fields:
                    value = row["values"].get(field["id"])
                    if value is None:
                        continue
                    if (
                        field["type"] == "reference"
                        and not db.execute(
                            "SELECT 1 FROM records WHERE dataset=? AND id=?",
                            (field["target"], value),
                        ).fetchone()
                    ):
                        fail("custom_page_reference_missing", 422)
                    if (
                        field["type"] == "attachment"
                        and not db.execute(
                            "SELECT 1 FROM attachments WHERE id=?", (value,)
                        ).fetchone()
                    ):
                        fail("custom_page_attachment_missing", 422)
                db.execute(
                    "INSERT INTO records VALUES (?,?,?,?,?) ON CONFLICT(dataset,id) DO UPDATE SET value=excluded.value,provenance=excluded.provenance,updated_at=excluded.updated_at",
                    (
                        dataset,
                        row["id"],
                        canonical(row["values"]),
                        canonical(row["provenance"]),
                        self.now(),
                    ),
                )
            self.quota(db)
            result = {"written": len(body["records"]), "revision": revision}
            self.receipt(db, key, fingerprint, result)
            self.quota(db)
            self.log(
                db,
                "records_written",
                {
                    "dataset": dataset,
                    "count": result["written"],
                    "kinds": sorted({r["provenance"]["kind"] for r in body["records"]}),
                    "run_ids": sorted(
                        {
                            r["provenance"]["run_id"]
                            for r in body["records"]
                            if r["provenance"]["run_id"]
                        }
                    ),
                },
            )
            return result

    @staticmethod
    def quota(db):
        rows, size = db.execute(
            "SELECT count(*),COALESCE(sum(length(CAST(value AS BLOB))+length(CAST(provenance AS BLOB))),0) FROM records"
        ).fetchone()
        size += db.execute("SELECT COALESCE(sum(length(content)),0) FROM attachments").fetchone()[0]
        size += db.execute(
            "SELECT COALESCE(sum(length(CAST(result AS BLOB))),0) FROM receipts"
        ).fetchone()[0]
        size += db.execute(
            "SELECT COALESCE(sum(length(CAST(manifest AS BLOB))),0) FROM revisions"
        ).fetchone()[0]
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='schedules'").fetchone():
            size += db.execute(
                "SELECT COALESCE(sum(length(approval)),0) FROM schedules"
            ).fetchone()[0]
        if rows > MAX_RECORDS or size > MAX_BYTES:
            fail("custom_page_quota_exceeded", 413)

    def query(self, agent_id, owner, query_id, body, validate_parameters):
        with self.database(agent_id, owner) as db:
            state = self.state(db)
            number = body.get("draft_revision") or state["active"]
            if not number:
                fail("custom_page_not_active")
            if body.get("expected_revision") not in (None, number):
                fail("custom_page_revision_conflict")
            candidate = self.revision(db, number)
            if candidate["status"] == "cancelled":
                fail("custom_page_draft_cancelled")
            manifest = candidate["manifest"]
            query = next((q for q in manifest["queries"] if q["id"] == query_id), None)
            if query is None:
                fail("custom_page_query_not_found", 404)
            validate_parameters(manifest, query, body["parameters"])
            allowed = {
                sqlite3.SQLITE_SELECT,
                sqlite3.SQLITE_READ,
                sqlite3.SQLITE_FUNCTION,
                sqlite3.SQLITE_TRANSACTION,
            }
            db.set_authorizer(
                lambda action, arg1, arg2, database, source: (
                    sqlite3.SQLITE_OK
                    if action in allowed
                    and database in (None, "main")
                    and (
                        action != sqlite3.SQLITE_FUNCTION
                        or arg2 in {"json_extract", "lower", "instr", "count"}
                    )
                    else sqlite3.SQLITE_DENY
                )
            )

            clauses = ["dataset=?"]
            args = [query["dataset"]]
            for key, value in body["parameters"].items():
                clauses.append("json_extract(value,?) IS ?")
                args.extend(["$." + key, value])
            if body["search"]:
                if not query["search"]:
                    fail("custom_page_search_unsupported", 422)
                clauses.append(
                    "("
                    + " OR ".join(
                        "instr(lower(CAST(json_extract(value,?) AS TEXT)),lower(?))>0"
                        for _ in query["search"]
                    )
                    + ")"
                )
                for field in query["search"]:
                    args.extend(["$." + field, body["search"]])
            where = " AND ".join(clauses)
            deadline = time.monotonic() + 2
            db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
            if query["operation"] == "count":
                rows = [
                    {
                        "count": db.execute(
                            "SELECT count(*) FROM records WHERE " + where, args
                        ).fetchone()[0]
                    }
                ]
            elif query["operation"] == "group":
                # An explicitly sorted grouping (e.g. publication dates) must
                # preserve domain order rather than connect largest counts first.
                order = (
                    ("label DESC" if query["descending"] else "label ASC")
                    if query["sort"] == query["group_by"]
                    else "count DESC,label"
                )
                rows = [
                    dict(r)
                    for r in db.execute(
                        "SELECT json_extract(value,?) AS label,count(*) AS count FROM records WHERE "
                        + where
                        + " GROUP BY label ORDER BY "
                        + order
                        + " LIMIT ? OFFSET ?",
                        ["$." + query["group_by"], *args, body["limit"] + 1, body["offset"]],
                    )
                ]
            else:
                order = "id"
                if query["sort"]:
                    order = (
                        "json_extract(value,?)"
                        + (" DESC" if query["descending"] else " ASC")
                        + ",id"
                    )
                    args.append("$." + query["sort"])
                rows = []
                for row in db.execute(
                    "SELECT id,value,provenance,updated_at FROM records WHERE "
                    + where
                    + " ORDER BY "
                    + order
                    + " LIMIT ? OFFSET ?",
                    [*args, body["limit"] + 1, body["offset"]],
                ):
                    value = json.loads(row["value"])
                    fields = query["fields"] or list(value)
                    rows.append(
                        {
                            "id": row["id"],
                            "values": {k: v for k, v in value.items() if k in fields},
                            "provenance": json.loads(row["provenance"]),
                            "updated_at": row["updated_at"],
                        }
                    )
            result = {
                "rows": rows[: body["limit"]],
                "next_offset": body["offset"] + body["limit"]
                if len(rows) > body["limit"]
                else None,
                "revision": number,
                "updated_at": state["updated_at"],
                "archived": state["status"] == "archived",
            }
            if len(canonical(result).encode()) > 1024 * 1024:
                fail("custom_page_result_too_large", 413)
            return result

    def activity(self, agent_id, owner):
        with self.database(agent_id, owner) as db:
            return [
                {**dict(r), "detail": json.loads(r["detail"])}
                for r in db.execute("SELECT * FROM activity ORDER BY id DESC LIMIT 100")
            ]

    def archive(self, agent_id, owner, expected):
        with (
            lifecycle_gate(self.files.data_dir, agent_id),
            self.database(agent_id, owner, write=True) as db,
        ):
            if self.state(db)["active"] != expected:
                fail("custom_page_revision_conflict")
            CustomPageScheduleRepository.quiescent(db, archive=True)
            db.execute("UPDATE app SET status='archived'")
            self.log(db, "archived", {"revision": expected})
            return {"archived": True, "retained": True}

    def cancel(self, agent_id, owner, number):
        with self.database(agent_id, owner, write=True) as db:
            row = self.revision(db, number)
            if row["status"] != "preview_ready":
                fail("custom_page_revision_conflict")
            db.execute("UPDATE revisions SET status='cancelled' WHERE revision=?", (number,))
            return {"cancelled": True}

    def backup(self, agent_id, owner):
        with self.lock(mutation=True), self.database(agent_id, owner) as db:
            path = self.directory(agent_id) / "backup.sqlite3"
            with sqlite3.connect(path) as destination:
                db.backup(destination)
            os.chmod(path, 0o600)
            return {"backup": "latest", "created_at": self.now(), "bytes": path.stat().st_size}

    def attachment(self, agent_id, owner, filename, content, key):
        fingerprint = digest({"filename": filename, "sha256": hashlib.sha256(content).hexdigest()})
        with self.database(agent_id, owner, write=True) as db:
            if self.state(db)["status"] != "active":
                fail("custom_page_not_active")
            result = self.replay(db, "attachment:" + key, fingerprint)
            if result is not None:
                return result
            ident = "att_" + fingerprint[7:39]
            db.execute(
                "INSERT OR IGNORE INTO attachments VALUES (?,?,?,?)",
                (ident, filename, fingerprint, content),
            )
            self.quota(db)
            result = {
                "id": ident,
                "filename": filename,
                "bytes": len(content),
                "digest": "sha256:" + hashlib.sha256(content).hexdigest(),
            }
            self.receipt(db, "attachment:" + key, fingerprint, result)
            self.quota(db)
            return result

    def read_attachment(self, agent_id, owner, ident):
        if not re.fullmatch(r"att_[0-9a-f]{32}", ident):
            fail("custom_page_attachment_not_found", 404)
        with self.database(agent_id, owner) as db:
            row = db.execute("SELECT * FROM attachments WHERE id=?", (ident,)).fetchone()
            if row is None:
                fail("custom_page_attachment_not_found", 404)
            return {
                "id": ident,
                "filename": row["filename"],
                "content_base64": base64.b64encode(row["content"]).decode(),
                "download_only": True,
            }

    def export(self, agent_id, owner):
        with self.database(agent_id, owner) as db:
            state = self.state(db)
            result = {
                "schema_version": 1,
                "state": state,
                "revisions": [
                    self.revision(db, r[0])
                    for r in db.execute("SELECT revision FROM revisions ORDER BY revision")
                ],
                "records": [
                    {
                        **dict(r),
                        "value": json.loads(r["value"]),
                        "provenance": json.loads(r["provenance"]),
                    }
                    for r in db.execute("SELECT * FROM records ORDER BY dataset,id")
                ],
                "attachments": [
                    {
                        "id": r["id"],
                        "filename": r["filename"],
                        "content_base64": base64.b64encode(r["content"]).decode(),
                    }
                    for r in db.execute("SELECT id,filename,content FROM attachments")
                ],
            }
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='schedules'").fetchone():
                result["schedules"] = [
                    {
                        "id": row["id"],
                        "approval": json.loads(row["approval"]),
                        "state": row["state"],
                        "attempts": row["attempts"],
                        "last_status": row["last_status"],
                    }
                    for row in db.execute("SELECT * FROM schedules")
                ]
            if len(canonical(result).encode()) > MAX_BYTES * 2:
                fail("custom_page_export_too_large", 413)
            return result

    def delete(self, agent_id, owner, expected):
        with lifecycle_gate(self.files.data_dir, agent_id), self.lock(mutation=True):
            with self.database(agent_id, owner, write=True) as db:
                state = self.state(db)
                CustomPageScheduleRepository.quiescent(db)
                if state["status"] != "archived" or state["active"] != expected:
                    fail("custom_page_archive_required")
            path = self.directory(agent_id)
            # Rename the entire app (including backups) before cleanup so readers
            # never open a partly deleted DB. The directory is Runtime-owned.
            import uuid

            tombstone = self.root / ("deleted-" + uuid.uuid4().hex)
            os.replace(path, tombstone)
            try:
                import shutil

                shutil.rmtree(tombstone)
            except OSError:
                return {"deleted": True, "cleanup_pending": True}
            return {"deleted": True, "cleanup_pending": False}

    def migration_plan(self, agent_id, owner, number, operations_for):
        with self.database(agent_id, owner) as db:
            state = self.state(db)
            if state["status"] != "active":
                fail("custom_page_not_active")
            target = self.revision(db, number)
            active = self.revision(db, state["active"])
            operations = operations_for(active["manifest"], target["manifest"])
            affected = {
                op["dataset"]: db.execute(
                    "SELECT count(*) FROM records WHERE dataset=?", (op["dataset"],)
                ).fetchone()[0]
                for op in operations
            }
            plan = {
                "expected_revision": state["active"],
                "revision": number,
                "digest": target["digest"],
                "operations": operations,
                "affected_records": affected,
            }
            return {**plan, "plan_digest": digest(plan)}

    def migrate(self, agent_id, owner, body, operations_for):
        with lifecycle_gate(self.files.data_dir, agent_id), self.lock(mutation=True):
            # WAL-aware consistent checkpoint; backup is retained for operator
            # recovery, not an implicit destructive restore API.
            self.backup(agent_id, owner)
            with self.database(agent_id, owner, write=True) as db:
                key = "migration:" + digest(body)
                replay = self.replay(db, key, digest(body))
                if replay is not None:
                    return replay
                state = self.state(db)
                target = self.revision(db, body["revision"])
                if (
                    state["status"] != "active"
                    or state["active"] != body["expected_revision"]
                    or target["base_revision"] != state["active"]
                    or target["status"] != "preview_ready"
                    or target["digest"] != body["digest"]
                ):
                    fail("custom_page_revision_conflict")
                CustomPageScheduleRepository.quiescent(db)
                active = self.revision(db, state["active"])
                operations = operations_for(active["manifest"], target["manifest"])
                affected = {
                    op["dataset"]: db.execute(
                        "SELECT count(*) FROM records WHERE dataset=?", (op["dataset"],)
                    ).fetchone()[0]
                    for op in operations
                }
                plan = {
                    "expected_revision": state["active"],
                    "revision": body["revision"],
                    "digest": body["digest"],
                    "operations": operations,
                    "affected_records": affected,
                }
                if operations != body["operations"] or digest(plan) != body["plan_digest"]:
                    fail("custom_page_migration_plan_stale")
                for op in operations:
                    if op["operation"] == "drop_dataset":
                        db.execute("DELETE FROM records WHERE dataset=?", (op["dataset"],))
                    else:
                        db.execute(
                            "UPDATE records SET value=json_remove(value,?) WHERE dataset=?",
                            ("$." + op["field"], op["dataset"]),
                        )
                db.execute(
                    "UPDATE revisions SET status='activated' WHERE revision=?", (body["revision"],)
                )
                db.execute("UPDATE app SET active=?,updated_at=?", (body["revision"], self.now()))
                self.log(
                    db,
                    "migration_applied",
                    {"revision": body["revision"], "operations": operations},
                )
                result = self.revision(db, body["revision"])
                self.receipt(db, key, digest(body), result)
                return result
