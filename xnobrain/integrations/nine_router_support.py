"""Client adapter for the local OmniRoute provider runtime."""
# ruff: noqa: F401

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import socket
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import quote

import aiohttp


OMNIROUTE_PROVIDER_KEY = "xnobrain"
OMNIROUTE_PROVIDER = f"custom:{OMNIROUTE_PROVIDER_KEY}"
OMNIROUTE_BASE_URL = "http://127.0.0.1:20128"
OMNIROUTE_API_BASE_URL = f"{OMNIROUTE_BASE_URL}/v1"
OMNIROUTE_KEY_ENV = "OMNIROUTE_API_KEY"
OMNIROUTE_DEFAULT_MODEL = "auto"
# Compatibility names keep existing profile migrations and third-party imports
# working while all newly written config uses the OmniRoute contract.
NINE_ROUTER_PROVIDER_KEY = OMNIROUTE_PROVIDER_KEY
NINE_ROUTER_PROVIDER = OMNIROUTE_PROVIDER
NINE_ROUTER_BASE_URL = OMNIROUTE_BASE_URL
NINE_ROUTER_API_BASE_URL = OMNIROUTE_API_BASE_URL
NINE_ROUTER_KEY_ENV = OMNIROUTE_KEY_ENV
NINE_ROUTER_DEFAULT_MODEL = OMNIROUTE_DEFAULT_MODEL
OPENCODE_ZEN_ROUTER_ALIAS = "ocz"
OPENCODE_ZEN_API_BASE_URL = "https://opencode.ai/zen/v1"

SUPPORTED_ROUTER_PROVIDERS = frozenset({
    "claude", "codex", "github", "cursor", "grok-cli", "antigravity",
    "openai", "anthropic", "gemini",
    "opencode-go", "opencode",
})
OAUTH_ROUTER_PROVIDERS = frozenset({
    "claude", "codex", "github", "grok-cli", "antigravity",
})
API_KEY_ROUTER_PROVIDERS = frozenset({
    "openai", "anthropic", "gemini", "opencode-go",
})
OPENAI_COMPATIBLE_PROVIDERS = frozenset(
    {"xai", "openrouter", "groq", "deepseek", "moonshot", "qwen", "openai-like"}
)
ROUTER_MODEL_ALIASES = {
    "claude": "cc",
    "codex": "cx",
    "github": "gh",
    "cursor": "cu",
    "grok-cli": "gc",
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
class OmniRouteAPIError(RuntimeError):
    """Expected local OmniRoute API failure."""

    def __init__(self, message: str, *, code: str = "provider_runtime_error", status: int = 502):
        super().__init__(message)
        self.code = code
        self.status = status


NineRouterAPIError = OmniRouteAPIError


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
        or OMNIROUTE_DEFAULT_MODEL
    ).strip()
    if not selected_model:
        selected_model = OMNIROUTE_DEFAULT_MODEL
    selected_model = route_nine_router_model(selected_model)

    model_config["provider"] = OMNIROUTE_PROVIDER
    model_config["default"] = selected_model
    model_config["base_url"] = OMNIROUTE_API_BASE_URL
    model_config.pop("model", None)

    config["providers"] = {
        OMNIROUTE_PROVIDER_KEY: {
            "name": "XNOBrain Provider Runtime",
            "api": OMNIROUTE_API_BASE_URL,
            "api_mode": "chat_completions",
            "default_model": selected_model,
            "model": selected_model,
            "key_env": OMNIROUTE_KEY_ENV,
            "request_timeout_seconds": 1800,
            "models": {selected_model: {}},
        }
    }
    config.pop("fallback_providers", None)
    return selected_model



__all__ = [name for name in globals() if not name.startswith("__")]
