"""Shared support for the centralized OpenAI-compatible LLM router client."""
# ruff: noqa: F401

from __future__ import annotations

import os
import re
from pathlib import Path
from collections.abc import Mapping
from typing import Any

import aiohttp


LLM_ROUTER_PROVIDER_KEY = "xnobrain"
LLM_ROUTER_PROVIDER = f"custom:{LLM_ROUTER_PROVIDER_KEY}"
LLM_ROUTER_BASE_URL = os.environ.get("RUNTIME_LLM_ROUTER_URL", "").rstrip("/")
LLM_ROUTER_API_BASE_URL = LLM_ROUTER_BASE_URL
LLM_ROUTER_KEY_ENV = "RUNTIME_LLM_API_KEY"
LLM_ROUTER_DEFAULT_MODEL = "auto"
OPENCODE_ZEN_ROUTER_ALIAS = "ocz"
OPENCODE_ZEN_API_BASE_URL = "https://opencode.ai/zen/v1"

ROUTER_MODEL_ALIASES = {
    "claude": "cc",
    "codex": "cx",
    "github": "gh",
    "cursor": "cu",
    "grok-cli": "gc",
    "xai-oauth": "xao",
    "kimi-coding": "kmc",
    "cline": "cl",
    "kilocode": "kc",
    "kiro": "kr",
    "amazon-q": "aq",
    "clinepass": "cp",
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


class LLMRouterAPIError(RuntimeError):
    """Expected centralized LLM router API failure."""

    def __init__(self, message: str, *, code: str = "provider_runtime_error", status: int = 502):
        super().__init__(message)
        self.code = code
        self.status = status

def route_llm_model(model: Any) -> str:
    model_id = str(model or "").strip()
    if not model_id.startswith("oc/"):
        return model_id
    upstream_id = model_id.removeprefix("oc/")
    return f"{OPENCODE_ZEN_ROUTER_ALIAS}/{upstream_id}"


def display_llm_model(model: Any) -> str:
    return str(model or "").strip()


def normalize_llm_router_config(
    config: dict[str, Any], model: str | None = None, *, selection_provider: str | None = None,
) -> str:
    """Force one Hermes provider while preserving unrelated profile settings."""

    model_config = config.get("model")
    if not isinstance(model_config, dict):
        model_config = {}
        config["model"] = model_config

    selected_model = str(
        model
        or model_config.get("default")
        or model_config.get("model")
        or LLM_ROUTER_DEFAULT_MODEL
    ).strip()
    if not selected_model:
        selected_model = LLM_ROUTER_DEFAULT_MODEL
    selected_model = route_llm_model(selected_model)

    current_selection = str(model_config.get("selection_provider") or "").strip().lower()
    if selection_provider is not None:
        current_selection = str(selection_provider or "").strip().lower()
    if current_selection and current_selection not in {"xnobrain", "auto"}:
        model_config["selection_provider"] = current_selection
    else:
        model_config.pop("selection_provider", None)
    model_config["provider"] = LLM_ROUTER_PROVIDER
    model_config["default"] = selected_model
    model_config["base_url"] = LLM_ROUTER_API_BASE_URL
    model_config.pop("model", None)

    assignment_id = str(model_config.get("assignment_id") or "").strip()
    if assignment_id and not _SAFE_ID_RE.fullmatch(assignment_id):
        assignment_id = ""
        model_config.pop("assignment_id", None)

    provider_config = {
            "name": "XNOBrain Provider Runtime",
            "api": LLM_ROUTER_API_BASE_URL,
            "api_mode": "chat_completions",
            "default_model": selected_model,
            "model": selected_model,
            "key_env": LLM_ROUTER_KEY_ENV,
            "request_timeout_seconds": 1800,
            "models": {selected_model: {}},
    }
    if assignment_id:
        provider_config["extra_headers"] = {
            "x-xnobrain-assignment-id": assignment_id,
        }
    config["providers"] = {LLM_ROUTER_PROVIDER_KEY: provider_config}
    config.pop("fallback_providers", None)
    return selected_model



__all__ = [name for name in globals() if not name.startswith("__")]
