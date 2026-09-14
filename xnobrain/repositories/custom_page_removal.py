"""Recovery plan for a retained app's original writer, never a replacement."""

from __future__ import annotations

import uuid

from ..models.custom_page import digest
from .base import StoreError


def fail(code):
    raise StoreError(code.replace("_", " "), status=409, code=code)


def profile_identity(path):
    if path.is_symlink():
        fail("custom_page_agent_replaced")
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    if not path.is_dir():
        fail("custom_page_agent_replaced")
    return [stat.st_dev, stat.st_ino]


def journal(db):
    if not db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='agent_removal'"
    ).fetchone():
        return None
    row = db.execute("SELECT * FROM agent_removal WHERE singleton=1").fetchone()
    return dict(row) if row else None


def initialize(db, profile, expected):
    db.execute(
        "CREATE TABLE IF NOT EXISTS agent_removal (singleton INTEGER PRIMARY KEY, state TEXT NOT NULL)"
    )
    columns = {row[1] for row in db.execute("PRAGMA table_info(agent_removal)")}
    for name, kind in (
        ("operation_id", "TEXT"),
        ("profile_device", "INTEGER"),
        ("profile_inode", "INTEGER"),
        ("expected_revision", "INTEGER"),
        ("recovery_digest", "TEXT"),
    ):
        if name not in columns:
            db.execute(f"ALTER TABLE agent_removal ADD COLUMN {name} {kind}")
    if journal(db) is None:
        identity = profile_identity(profile)
        if identity is None:
            fail("custom_page_agent_removal_unverified")
        db.execute(
            "INSERT INTO agent_removal(singleton,state,operation_id,profile_device,profile_inode,expected_revision) VALUES (1,'pending',?,?,?,?)",
            ("rem_" + uuid.uuid4().hex, *identity, expected),
        )


def plan(db, profile, agent):
    receipt = journal(db)
    state = db.execute("SELECT active,status FROM app WHERE singleton=1").fetchone()
    base = {
        "state": "none",
        "operation_id": None,
        "expected_revision": state["active"],
        "profile_state": "unverified",
        "next_action": "none",
        "digest": None,
    }
    if receipt is None:
        return base
    if receipt.get("state") not in {"pending", "complete"}:
        fail("custom_page_agent_removal_unverified")
    identity = profile_identity(profile)
    if (
        not receipt.get("operation_id")
        or receipt.get("expected_revision") is None
        or not receipt.get("profile_inode")
    ):
        return {**base, "state": "blocked"}
    matches = identity == [receipt["profile_device"], receipt["profile_inode"]]
    profile_state = "missing" if identity is None else "original" if matches else "replaced"
    base.update(operation_id=receipt["operation_id"], profile_state=profile_state)
    if (
        profile_state == "replaced"
        or state["status"] != "archived"
        or state["active"] != receipt["expected_revision"]
    ):
        return {**base, "state": "blocked"}
    if receipt["state"] == "complete":
        return {**base, "state": "complete" if profile_state == "missing" else "blocked"}
    base.update(
        state="recoverable",
        next_action="finalize" if profile_state == "missing" else "remove_original",
    )
    base["digest"] = digest(
        {
            "agent_id": agent,
            **base,
            "profile_device": receipt["profile_device"],
            "profile_inode": receipt["profile_inode"],
        }
    )
    return base
