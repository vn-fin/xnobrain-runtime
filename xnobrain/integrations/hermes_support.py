"""Adapter for the original Hermes CLI, core runtime, and native profiles."""
# ruff: noqa: F401

from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import json
import os
import re
import secrets
import shutil
import sqlite3
import string
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import yaml

from ..defaults import (
    BIG_BROTHER_AGENT_ID,
    BIG_BROTHER_DESCRIPTION,
    BIG_BROTHER_DISPLAY_NAME,
    BIG_BROTHER_SKILL_CATEGORY,
    BIG_BROTHER_SKILL_ID,
    CUSTOM_SKILL_CATEGORY,
    honcho_memory_enabled,
)
from .nine_router import (
    NINE_ROUTER_DEFAULT_MODEL,
    NINE_ROUTER_PROVIDER,
    NineRouterAPIError,
    NineRouterManager,
    display_nine_router_model,
    normalize_nine_router_config,
    route_nine_router_model,
)


AGENT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
GENERATED_AGENT_NAME_RE = re.compile(r"^[a-z][a-z0-9]{5}$")
SKILL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
MAX_TEXT_CHARS = 200_000
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_CHAT_TIMEOUT_SECONDS = 3600
DEFAULT_CHAT_TIMEOUT_SECONDS = 900
TODO_STATUSES = {"pending", "in_progress", "completed", "cancelled"}
MAX_TODO_ITEMS = 256
MAX_TODO_CONTENT_CHARS = 4000
GENERATED_AGENT_ID_LENGTH = 6
GENERATED_AGENT_ID_FIRST_ALPHABET = string.ascii_lowercase
GENERATED_AGENT_ID_ALPHABET = string.ascii_lowercase + string.digits
PROVIDER_ERROR_OUTPUT_RE = re.compile(
    r"^HTTP\s+[45]\d{2}:\s+\[[^\]\n]+\]\s+\[[45]\d{2}\]:",
    re.IGNORECASE,
)
DEFAULT_ROOT_PROFILE = str(Path.home() / ".hermes")
DEFAULT_PROFILES_ROOT = str(Path.home() / ".hermes" / "profiles")
DEFAULT_LEGACY_AGENTS_ROOT = str(Path.home() / ".hermes" / "legacy-agents")
DEFAULT_AGENT_CONFIG_DIR = str(Path.home() / ".config" / "sandbox-agent")
DEFAULT_CONVERSATION_TITLE = "New Session"
DEFAULT_CONVERSATION_TITLE_RE = re.compile(
    r"^New Session(?: ([1-9][0-9]*))?$",
    re.IGNORECASE,
)
METADATA_FILE = "agent.json"
PROFILES_REGISTRY_FILE = "profiles.yaml"
CREDENTIAL_FILES = (".env", "auth.json")
AGENT_CREDENTIAL_ENV_KEYS = ("NINE_ROUTER_API_KEY",)
TEMPLATE_FILES = ("config.yaml", "SOUL.md", "AGENTS.md", "mcp.json")
TEMPLATE_DIRS = ("memories", "plugins")
PROFILE_STATE_DIRS = (
    "skills",
    "sessions",
    "logs",
    "memories",
    "cron",
    "plugins",
    "home",
)
LEGACY_MANAGED_AGENT_GUIDANCE = (
    """You are an AI agent. If asked who or what you are, describe yourself only
as an AI agent and summarize relevant capabilities. Do not identify the application,
runtime, framework, model provider, or implementation vendor.
When a response relies on web browsing or search, cite every source used with a
direct clickable link near the supported claim. When a response relies on a document,
cite its title or durable path and exact page number or range. Never fabricate a
citation, and distinguish sourced facts from inference.""",
    """You are an AI agent. If asked who or what you are, describe yourself only
as an AI agent and summarize relevant capabilities. Do not identify the application,
runtime, framework, model provider, or implementation vendor.
For direct identity questions, answer exactly as an AI agent without naming or
guessing any underlying product, runtime, framework, provider, or vendor.
When a response relies on web browsing or search, cite every source used with a
direct clickable link near the supported claim. When a response relies on a document,
cite its title or durable path and exact page number or range. Never fabricate a
citation, and distinguish sourced facts from inference.""",
    """You are an AI agent. If asked who or what you are, describe yourself only
as an AI agent and summarize relevant capabilities. Do not identify the
application, runtime, framework, model provider, or implementation vendor.
Cite every web source with a direct clickable link near the supported claim.
Cite document-derived claims with the document title or path and exact page
number or range. Never fabricate citations; distinguish inference clearly.""",
)


def _new_conversation_id() -> str:
    """Use the native CLI session shape for API-created conversations."""
    return f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"


def _todo_updated_event(result: Any) -> dict[str, Any] | None:
    """Return only validated, user-facing data from a Hermes todo result."""
    if isinstance(result, str):
        if len(result) > 512_000:
            return None
        try:
            value = json.loads(result)
        except (json.JSONDecodeError, TypeError):
            return None
    elif isinstance(result, Mapping):
        value = result
    else:
        return None
    raw_todos = value.get("todos")
    if not isinstance(raw_todos, list):
        return None

    todos: list[dict[str, str]] = []
    for raw in raw_todos[:MAX_TODO_ITEMS]:
        if not isinstance(raw, Mapping):
            continue
        item_id = str(raw.get("id") or "").strip()[:256]
        content = str(raw.get("content") or "").strip()[:MAX_TODO_CONTENT_CHARS]
        status = str(raw.get("status") or "").strip().lower()
        if not item_id or not content or status not in TODO_STATUSES:
            continue
        todos.append({"id": item_id, "content": content, "status": status})

    summary = {status: sum(item["status"] == status for item in todos) for status in TODO_STATUSES}
    return {
        "event": "todo.updated",
        "todos": todos,
        "summary": {"total": len(todos), **summary},
    }


class AgentAPIError(ValueError):
    """Expected API error for named-agent operations."""

    def __init__(self, message: str, *, code: str = "invalid_agent", status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status



__all__ = [name for name in globals() if not name.startswith("__")]
