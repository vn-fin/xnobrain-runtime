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
    "web",
)
BIG_BROTHER_APPROVAL_DEFAULT_MARKER = "approval_default_initialized"
BIG_BROTHER_MODEL_DEFAULT_MARKER = "model_default_initialized"
DEFAULT_PROFILE_MODEL = "auto"
LEGACY_BIG_BROTHER_TOOLSET = "xnobrain-control"


def memory_enabled() -> bool:
    """Return whether the embedded memory subsystem is enabled by default."""
    return os.environ.get("RUNTIME_HONCHO_MEMORY_ENABLE", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
