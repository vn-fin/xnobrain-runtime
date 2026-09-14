"""Run one explicitly requested layout-only task; never approve a managed layout."""

from __future__ import annotations

import json

from ..models.ui_composition import AssistLayout
from ..repositories.ui_composition import UICompositionRepository, fail
from ..services.custom_page import validated
from .base import ServiceError

NAVIGATION_OPTIONS = (
    "home",
    "marketplace",
    "connections",
    "skills",
    "skill-community",
    "kanban",
    "cron",
    "teams",
    "analytics",
    "settings",
    "agents",
    "files",
    "members",
    "member-teams",
    "boards",
    "delegations",
    "usage",
    "audit",
    "llm",
)

INSPECTOR_POSITIONS = ("right", "left", "bottom")

ACCENT_OPTIONS = ("default", "teal", "blue", "violet")


class UICompositionService:
    def __init__(self, platform):
        self.platform = platform
        self.repository = UICompositionRepository(platform.repository)

    def owner(self, trusted):
        if not trusted or not trusted.subject or not trusted.tenant_id:
            fail("ui_assistance_identity_required", 401)
        return trusted.tenant_id + "\0" + trusted.subject

    def context(self, body, trusted):
        self.owner(trusted)
        agent = body["agent_id"]
        from .conversation_authority import require_binding

        try:
            stored = require_binding(
                self.platform.repository, agent, body["conversation_id"], trusted, active=True
            )
        except ServiceError:
            fail("ui_assistance_conversation_forbidden", 403)
        scope = body["context"]
        if (scope == "personal" and stored.get("owner_kind") != "personal") or (
            scope != "personal"
            and (
                stored.get("owner_kind") != "organization" or stored.get("organization_id") != scope
            )
        ):
            fail("ui_assistance_context_mismatch", 403)
        self.platform.agents.get_conversation(agent, body["conversation_id"])
        return stored

    @staticmethod
    def validate_layout(layout, catalog):
        if not isinstance(layout, dict) or len(json.dumps(layout).encode()) > 16384:
            fail()
        keys = {
            "inspector_position",
            "inspector_height",
            "navigation",
            "default_page",
            "schema_version",
            "name",
            "preset",
            "accent",
            "theme",
            "density",
            "font_scale",
            "widgets",
        }
        if not layout.keys() <= keys or layout.get("schema_version") != 1:
            fail()
        name = layout.get("name")
        if not isinstance(name, str) or not name.strip() or len(name.encode()) > 80:
            fail()
        if layout.get("preset") not in {"stacked", "two-column"}:
            fail()
        if (
            "inspector_position" in layout
            and layout["inspector_position"] not in INSPECTOR_POSITIONS
        ):
            fail()
        if "inspector_height" in layout and (
            type(layout["inspector_height"]) is not int
            or not 160 <= layout["inspector_height"] <= 480
        ):
            fail()
        if "default_page" in layout and layout["default_page"] not in NAVIGATION_OPTIONS:
            fail()
        if "navigation" in layout:
            navigation = layout["navigation"]
            if not isinstance(navigation, dict) or set(navigation) != {"order", "hidden"}:
                fail()
            for entries in navigation.values():
                if (
                    not isinstance(entries, list)
                    or len(entries) > len(NAVIGATION_OPTIONS)
                    or any(
                        not isinstance(item, str) or item not in NAVIGATION_OPTIONS
                        for item in entries
                    )
                    or len(set(entries)) != len(entries)
                ):
                    fail()
            if any(item in {"home", "settings"} for item in navigation["hidden"]):
                fail()
        if "accent" in layout and layout["accent"] not in ACCENT_OPTIONS:
            fail()
        if "theme" in layout and layout["theme"] not in {"auto", "light", "dark"}:
            fail()
        if "density" in layout and layout["density"] not in {"comfortable", "compact"}:
            fail()
        if "font_scale" in layout and (
            type(layout["font_scale"]) not in (float, int)
            or not 0.9 <= layout["font_scale"] <= 1.25
        ):
            fail()
        widgets = layout.get("widgets")
        if not isinstance(widgets, list) or len(widgets) > 12:
            fail()
        import re

        seen = set()
        for widget in widgets:
            if not isinstance(widget, dict) or set(widget) != {"id", "kind", "slot"}:
                fail()
            ident = widget.get("id")
            if (
                not isinstance(ident, str)
                or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}", ident)
                or ident in seen
            ):
                fail()
            if not any(
                spec.get("kind") == widget["kind"]
                and widget["slot"] in spec.get("slots", [])
                and spec.get("version") == 1
                for spec in catalog
            ):
                fail()
            seen.add(ident)

    async def start(self, body, trusted):
        from ..runtime_limits import session_timeout_seconds

        updates = getattr(self.platform, "runtime_updates", None)
        if updates is not None:
            updates.require_dispatch()
        body = validated(AssistLayout, body)
        owner = self.owner(trusted)
        self.context(body, trusted)
        self.validate_layout(body["layout"], body["catalog"])
        if len(json.dumps(body).encode()) > 32768:
            fail("ui_assistance_limit", 413)
        row = self.repository.prepare(owner, body)
        if row["cancelled"]:
            fail("ui_assistance_cancelled")
        if row["run_id"]:
            return self.read(body["assistance_id"], trusted)

        def guard():
            self.context(body, trusted)
            if self.repository.get(owner, body["assistance_id"])["cancelled"]:
                fail("ui_assistance_cancelled")

        run = await self.platform.start_conversation_run(
            body["agent_id"],
            body["conversation_id"],
            {
                "input": "Propose a declarative XNOBrain UI layout for this explicit request. Use ui_layout_catalog and ui_layout_propose. No code, data collection, activation, or other work.\nRequest: "
                + body["request"],
                "idempotency_key": body["assistance_id"],
                "timeout_seconds": session_timeout_seconds(body["timeout_seconds"]),
                "run_mode": "background",
            },
            trusted,
            dispatch_guard=guard,
            ui_assistance={"id": body["assistance_id"], "owner": owner},
        )
        try:
            self.repository.change(owner, body["assistance_id"], run_id=run["id"])
        except Exception:
            await self.platform.conversation_runs.cancel_run(
                body["agent_id"], body["conversation_id"], run["id"]
            )
            raise
        return self.read(body["assistance_id"], trusted)

    def read(self, identifier, trusted):
        owner = self.owner(trusted)
        row = self.repository.get(owner, identifier)
        if row["request"] is None:
            fail("ui_assistance_cancelled")
        self.context(row["request"], trusted)
        run = (
            self.platform.conversation_runs.get_run(
                row["request"]["agent_id"], row["request"]["conversation_id"], row["run_id"]
            )
            if row["run_id"]
            else None
        )
        return {
            "assistance_id": identifier,
            "layout_id": row["request"]["layout_id"],
            "base_revision": row["request"]["base_revision"],
            "run_id": row["run_id"],
            "status": "cancelled" if row["cancelled"] else run["status"] if run else "preparing",
            "layout": row["result"]
            if run and run["status"] == "completed" and not row["cancelled"]
            else None,
            "payer_kind": self.context(row["request"], trusted).get("payer_kind"),
        }

    async def cancel(self, identifier, trusted):
        owner = self.owner(trusted)
        row = self.repository.cancel(owner, identifier)
        if row["request"] is None:
            return {
                "assistance_id": identifier,
                "layout_id": "",
                "base_revision": 0,
                "run_id": None,
                "status": "cancelled",
                "layout": None,
                "payer_kind": "unknown",
            }
        if row["run_id"]:
            run = self.platform.conversation_runs.get_run(
                row["request"]["agent_id"], row["request"]["conversation_id"], row["run_id"]
            )
            if run["status"] not in {"completed", "failed", "cancelled", "timed_out"}:
                await self.platform.conversation_runs.cancel_run(
                    run["agent_id"], run["conversation_id"], run["id"]
                )
        return {
            "assistance_id": identifier,
            "layout_id": row["request"]["layout_id"],
            "base_revision": row["request"]["base_revision"],
            "run_id": row["run_id"],
            "status": "cancelled",
            "layout": None,
            "payer_kind": "unknown",
        }
