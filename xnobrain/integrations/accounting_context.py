"""Request-local workload credentials and attribution. No process env mutation."""

import os
import re
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from .router_accounting import AccountingUnavailable

_CURRENT = ContextVar("xnobrain_accounting", default=None)
_OPAQUE = re.compile(r"^[A-Za-z0-9._:/-]{1,128}$")


def accounting_enabled():
    mode = os.environ.get("RUNTIME_ACCOUNTING_MODE", "legacy").strip()
    if mode not in {"legacy", "router"}:
        raise AccountingUnavailable("Unsupported accounting mode")
    return mode == "router"


def accounting_binding(agent_id, context_id="personal"):
    if context_id != "personal":
        raise AccountingUnavailable("Organization accounting is not activated")
    agent_id = str(agent_id or "").strip()
    if not _OPAQUE.fullmatch(agent_id):
        raise AccountingUnavailable("Agent accounting correlation is invalid")
    token = os.environ.get("RUNTIME_LLM_API_KEY", "").strip()
    token_file = os.environ.get("RUNTIME_LLM_API_KEY_FILE", "").strip()
    if token_file:
        try:
            path = Path(token_file)
            if path.is_symlink() or path.stat().st_mode & 0o077:
                raise ValueError
            token = path.read_text(encoding="utf-8").strip() or token
        except (OSError, ValueError) as error:
            raise AccountingUnavailable("Router user key unavailable") from error
    if not token:
        raise AccountingUnavailable("Router user key unavailable")
    return {
        "local_agent_id": agent_id,
        "context_id": context_id,
        "agent_id": agent_id,
        "user_key": token,
        "capability_version": "gorouter-user-usage-v1",
    }


def current_accounting():
    return _CURRENT.get()


@contextmanager
def inference_accounting(binding, conversation_id="", run_id="", parent_run_id=""):
    headers = {"X-GoRouter-Agent-Id": binding["agent_id"]}
    for name, value in [
        ("Conversation", conversation_id),
        ("Run", run_id),
        ("Parent-Run", parent_run_id),
    ]:
        if value:
            if not _OPAQUE.fullmatch(value):
                raise AccountingUnavailable("Invalid accounting correlation")
            headers[f"X-GoRouter-{name}-Id"] = value
    # Router generates a fresh logical request ID per call. A conversation/run
    # ID is never reused as a request/idempotency key across model turns.
    token = _CURRENT.set({"binding": dict(binding), "headers": headers})
    try:
        yield
    finally:
        _CURRENT.reset(token)
