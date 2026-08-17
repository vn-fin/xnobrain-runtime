"""Agents API route declarations."""

from ..models import AgentCreate, AgentMetadataPatch, ConfigPatch, EnabledPatch, MemoryPatch, SkillInstall, SkillSyncRequest
from .definition import route

ROUTES = (
    route("GET", "/agents", "agents_list", tags=("Agents",)),
    route("GET", "/agents/activity", "agents_activity", tags=("Agents",)),
    route(
        "GET",
        "/agents/activity/stream",
        "agents_activity_stream",
        special="agent_activity_stream",
        tags=("Agents",),
    ),
    route("POST", "/agents", "agents_create", AgentCreate, tags=("Agents",)),
    route("GET", "/profiles", "profiles_list", tags=("Profiles",)),
    route("GET", "/agents/{agent_id}/detail", "agents_get", tags=("Agents",)),
    route("GET", "/agents/{agent_id}/runtime", "agents_get", tags=("Agents",)),
    route("PATCH", "/agents/{agent_id}/metadata", "agents_metadata", AgentMetadataPatch, tags=("Agents",)),
    route("DELETE", "/agents/{agent_id}/delete", "agents_delete", tags=("Agents",)),
    route("POST", "/agents/{agent_id}/test", "agents_test", tags=("Agents",)),
    route("GET", "/agents-configs/global", "config_global_get", tags=("Config",)),
    route("PATCH", "/agents-configs/global", "config_global_patch", ConfigPatch, tags=("Config",)),
    route("PATCH", "/agents-configs/{agent_id}", "config_agent_patch", ConfigPatch, tags=("Config",)),
    route("GET", "/agents-skills", "skills_default_list", tags=("Skills",)),
    route("POST", "/agents-skills", "skills_default_install", SkillInstall, tags=("Skills",)),
    route("POST", "/agents-skills/sync/preview", "skills_sync_preview", SkillSyncRequest, tags=("Skills",)),
    route("POST", "/agents-skills/sync", "skills_sync", SkillSyncRequest, tags=("Skills",)),
    route("PATCH", "/agents-skills/{skill_id}", "skills_default_patch", EnabledPatch, tags=("Skills",)),
    route("GET", "/agents-skills/{agent_id}", "skills_list", tags=("Skills",)),
    route("POST", "/agents-skills/{agent_id}", "skills_install", SkillInstall, tags=("Skills",)),
    route("PATCH", "/agents-skills/{agent_id}/{skill_id}", "skills_patch", EnabledPatch, tags=("Skills",)),
    route("DELETE", "/agents-skills/{agent_id}/{skill_id}", "skills_delete", tags=("Skills",)),
    route("GET", "/agents/{agent_id}/memory", "memory_get", tags=("Memory",)),
    route("PATCH", "/agents/{agent_id}/memory", "memory_patch", MemoryPatch, tags=("Memory",)),
    route("GET", "/agents/{agent_id}/snapshots", "snapshots_list", tags=("Snapshots",)),
    route("POST", "/agents/{agent_id}/snapshots/{snapshot_id}/restore", "snapshots_restore", tags=("Snapshots",)),
)
