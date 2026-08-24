"""Adapter for Hermes root-profile configuration APIs and files."""
# ruff: noqa: F401

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping

import yaml

from ..defaults import CUSTOM_SKILL_CATEGORY
from .llm_router import (
    _SAFE_ID_RE,
    LLM_ROUTER_API_BASE_URL,
    LLM_ROUTER_DEFAULT_MODEL,
    LLM_ROUTER_PROVIDER,
    LLM_ROUTER_PROVIDER_KEY,
    display_llm_model,
    normalize_llm_router_config,
)


SKILL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
MAX_TEXT_CHARS = 200_000
MAX_CONFIG_STRING_CHARS = 100_000
MAX_INSTALL_TIMEOUT_SECONDS = 600
DEFAULT_INSTALL_TIMEOUT_SECONDS = 180
DEFAULT_ROOT_PROFILE = str(Path.home() / ".hermes")
DEFAULT_SOUL = (
    "You are a trusted personal assistant for one person. Be useful, discreet, "
    "and clear. Help with planning, writing, research, analysis, decisions, "
    "everyday operations, and technical work when needed.\n\n"
    "Work with practical judgment. Ask a focused question when the task is "
    "ambiguous, make reasonable assumptions when the risk is low, and explain "
    "uncertainty plainly. Keep responses compact by default, but give enough "
    "detail for the user to act confidently.\n"
)

_MISSING = object()


class ConfigAPIError(ValueError):
    """Expected API error for root profile config operations."""

    def __init__(self, message: str, *, code: str = "invalid_config", status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status



__all__ = [name for name in globals() if not name.startswith("__")]
