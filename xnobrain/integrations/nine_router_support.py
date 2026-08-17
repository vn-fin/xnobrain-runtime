"""Client adapter for 9router providers, models, OAuth, and quotas."""
# ruff: noqa: F401

from __future__ import annotations

import hashlib
import os
import re
import secrets
import socket
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import quote

import aiohttp


NINE_ROUTER_PROVIDER_KEY = "xnobrain"
NINE_ROUTER_PROVIDER = f"custom:{NINE_ROUTER_PROVIDER_KEY}"
NINE_ROUTER_BASE_URL = "http://127.0.0.1:20128"
NINE_ROUTER_API_BASE_URL = f"{NINE_ROUTER_BASE_URL}/v1"
NINE_ROUTER_KEY_ENV = "NINE_ROUTER_API_KEY"
NINE_ROUTER_DEFAULT_MODEL = "auto"
OPENCODE_ZEN_ROUTER_ALIAS = "ocz"
OPENCODE_ZEN_API_BASE_URL = "https://opencode.ai/zen/v1"

SUPPORTED_ROUTER_PROVIDERS = frozenset({
    "claude", "codex", "antigravity", "openai", "anthropic", "gemini",
    "opencode-go", "opencode",
})
OAUTH_ROUTER_PROVIDERS = frozenset({"claude", "codex", "antigravity"})
API_KEY_ROUTER_PROVIDERS = frozenset({
    "openai", "anthropic", "gemini", "opencode-go",
})
OPENAI_COMPATIBLE_PROVIDERS = frozenset(
    {"xai", "openrouter", "groq", "deepseek", "moonshot", "qwen", "openai-like"}
)
ROUTER_MODEL_ALIASES = {
    "claude": "cc",
    "codex": "cx",
    "antigravity": "ag",
    "openai": "openai",
    "anthropic": "anthropic",
    "gemini": "gemini",
    "opencode-go": "ocg",
    "opencode": "oc",
}
ROUTER_PROVIDER_BY_MODEL_OWNER = {
    owner: provider for provider, owner in ROUTER_MODEL_ALIASES.items()
}
ROUTER_PROVIDER_BY_MODEL_OWNER[OPENCODE_ZEN_ROUTER_ALIAS] = "opencode"

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$")
_OAUTH_GET_ACTIONS = frozenset(
    {"authorize", "device-code", "start-proxy", "poll-status", "stop-proxy"}
)
_OAUTH_POST_ACTIONS = frozenset({"exchange", "poll", "manual-code"})
class NineRouterAPIError(RuntimeError):
    """Expected local 9Router API failure."""

    def __init__(self, message: str, *, code: str = "provider_runtime_error", status: int = 502):
        super().__init__(message)
        self.code = code
        self.status = status


def route_nine_router_model(model: Any) -> str:
    model_id = str(model or "").strip()
    if not model_id.startswith("oc/"):
        return model_id
    upstream_id = model_id.removeprefix("oc/")
    return f"{OPENCODE_ZEN_ROUTER_ALIAS}/{upstream_id}"


def display_nine_router_model(model: Any) -> str:
    return str(model or "").strip()


def normalize_nine_router_config(config: dict[str, Any], model: str | None = None) -> str:
    """Force one Hermes provider while preserving unrelated profile settings."""

    model_config = config.get("model")
    if not isinstance(model_config, dict):
        model_config = {}
        config["model"] = model_config

    selected_model = str(
        model
        or model_config.get("default")
        or model_config.get("model")
        or NINE_ROUTER_DEFAULT_MODEL
    ).strip()
    if not selected_model:
        selected_model = NINE_ROUTER_DEFAULT_MODEL
    selected_model = route_nine_router_model(selected_model)

    model_config["provider"] = NINE_ROUTER_PROVIDER
    model_config["default"] = selected_model
    model_config["base_url"] = NINE_ROUTER_API_BASE_URL
    model_config.pop("model", None)

    config["providers"] = {
        NINE_ROUTER_PROVIDER_KEY: {
            "name": "XNOBrain Provider Runtime",
            "api": NINE_ROUTER_API_BASE_URL,
            "api_mode": "chat_completions",
            "default_model": selected_model,
            "model": selected_model,
            "key_env": NINE_ROUTER_KEY_ENV,
            "request_timeout_seconds": 1800,
            "models": {selected_model: {}},
        }
    }
    config.pop("fallback_providers", None)
    return selected_model



__all__ = [name for name in globals() if not name.startswith("__")]
