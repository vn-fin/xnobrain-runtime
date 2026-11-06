"""Product-owned defaults that stay stable across profiles and APIs."""

import os

BIG_BROTHER_AGENT_ID = "big-brother"
BIG_BROTHER_DISPLAY_NAME = "Big Brother"
BIG_BROTHER_DESCRIPTION = "Platform coordinator for agents, skills, usage, and Kanban work."
BIG_BROTHER_SKILL_ID = "big-brother-control"
BIG_BROTHER_SKILL_CATEGORY = "big-brother"
CUSTOM_SKILL_CATEGORY = "custom"
BIG_BROTHER_NATIVE_TOOLSETS = (
    "browser",
    "clarify",
    "code_execution",
    "computer_use",
    "cronjob",
    "delegation",
    "file",
    "image_gen",
    "kanban",
    "memory",
    "session_search",
    "skills",
    "terminal",
    "todo",
    "tts",
    "vision",
    "xnobrain_runtime_updates",
    "web",
)
BIG_BROTHER_APPROVAL_DEFAULT_MARKER = "approval_default_initialized"
BIG_BROTHER_MODEL_DEFAULT_MARKER = "model_default_initialized"
DEFAULT_PROFILE_MODEL = "auto"
LEGACY_BIG_BROTHER_TOOLSET = "xnobrain-control"


def pin_api_server_disabled(config: dict) -> bool:
    """Keep the gateway's ``api_server`` listener off unless configured.

    Any usable ``API_SERVER_KEY`` in the environment force-enables Hermes'
    ``api_server`` platform. Runtime owns the API port, so the workspace
    profile must carry an explicit ``platforms.api_server.enabled: false``.
    Only fills an absent value; an operator's explicit choice wins.
    """
    platforms = config.get("platforms")
    if not isinstance(platforms, dict):
        platforms = {}
        config["platforms"] = platforms
    api_server = platforms.get("api_server")
    if not isinstance(api_server, dict):
        api_server = {}
        platforms["api_server"] = api_server
    if "enabled" in api_server:
        return False
    api_server["enabled"] = False
    return True


def memory_enabled() -> bool:
    """Return whether the embedded memory subsystem is enabled by default."""
    return os.environ.get("RUNTIME_HONCHO_MEMORY_ENABLE", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
