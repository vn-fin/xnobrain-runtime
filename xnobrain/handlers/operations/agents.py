"""Feature-owned operation handlers."""

import time
from typing import Any, Callable

from ..query import bucket, csv, time_range

Operation = tuple[Callable[[], Any], str, int]


def operations(handler: Any, request: Any, body: dict[str, Any]) -> dict[str, Operation]:
    p, q, s = request.path_params, request.query_params, handler.service
    agent = lambda: (
        str(q.get("agent") or "").strip() or (_ for _ in ()).throw(ValueError("agent is required"))
    )
    skills_catalog = (
        s.list_skills_overview
        if str(q.get("include_agents") or "").strip().lower() in {"1", "true", "yes"}
        else s.list_default_skills
    )
    return {
        "agents_list": (s.list_agents_async, "agents retrieved successfully", 200),
        "agents_activity": (s.agent_activity, "agent activity retrieved successfully", 200),
        "agents_create": (lambda: s.create_agent(body), "agent created successfully", 201),
        "profiles_list": (s.list_profiles, "profiles retrieved successfully", 200),
        "agents_get": (lambda: s.get_agent(p["agent_id"]), "agent retrieved successfully", 200),
        "agents_metadata": (
            lambda: s.update_agent_metadata(p["agent_id"], body),
            "agent updated successfully",
            200,
        ),
        "agents_delete": (lambda: s.delete_agent(p["agent_id"]), "agent deleted successfully", 200),
        "agents_test": (
            lambda: {"agent_id": s.get_agent(p["agent_id"])["id"], "healthy": True, "status": "ok"},
            "agent tested successfully",
            200,
        ),
        "config_global_get": (s.global_config, "global config retrieved successfully", 200),
        "config_global_patch": (
            lambda: s.update_global_config(body),
            "global config updated successfully",
            200,
        ),
        "config_agent_patch": (
            lambda: s.update_agent_config(p["agent_id"], body),
            "agent config updated successfully",
            200,
        ),
        "skills_default_list": (skills_catalog, "skills retrieved successfully", 200),
        "skills_default_install": (
            lambda: s.install_default_skill(body),
            "skill installed into default profile",
            201,
        ),
        "skills_sync_preview": (
            lambda: s.preview_skill_sync(body),
            "skill sync preview generated successfully",
            200,
        ),
        "skills_sync": (lambda: s.sync_skills(body), "skills synchronized successfully", 200),
        "skills_default_patch": (
            lambda: s.set_default_skill_enabled(p["skill_id"], body),
            "default profile skill updated successfully",
            200,
        ),
        "skills_list": (lambda: s.list_skills(p["agent_id"]), "skills retrieved successfully", 200),
        "skills_install": (
            lambda: s.install_skill(p["agent_id"], body),
            "skill installed successfully",
            201,
        ),
        "skills_patch": (
            lambda: s.set_skill_enabled(p["agent_id"], p["skill_id"], body),
            "skill updated successfully",
            200,
        ),
        "skills_delete": (
            lambda: s.remove_skill(p["agent_id"], p["skill_id"]),
            "skill removed successfully",
            200,
        ),
        "memory_get": (lambda: s.read_memory(p["agent_id"]), "memory retrieved successfully", 200),
        "memory_patch": (
            lambda: s.write_memory(p["agent_id"], body),
            "memory updated successfully",
            200,
        ),
        "snapshots_list": (
            lambda: s.list_snapshots(p["agent_id"], q.get("kind")),
            "snapshots retrieved successfully",
            200,
        ),
        "snapshots_restore": (
            lambda: s.restore_snapshot(p["agent_id"], p["snapshot_id"]),
            "snapshot restored successfully",
            200,
        ),
    }
