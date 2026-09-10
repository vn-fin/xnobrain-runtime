"""Request-local workload credentials and attribution. No process env mutation."""

import json
import os
import re
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from .router_accounting import AccountingUnavailable

_CURRENT = ContextVar("xnobrain_accounting", default=None)
_OPAQUE = re.compile(r"^[A-Za-z0-9._:/-]{1,128}$")


def accounting_enabled():
    return bool(os.environ.get("RUNTIME_LLM_ROUTER_URL", "").strip())


def accounting_binding(agent_id, context_id="personal"):
    # A Control lifecycle provisioner must install this protected mapping. This
    # read cannot mint credentials or infer identities from names/current keys.
    configured = os.environ.get("RUNTIME_ACCOUNTING_BINDINGS_FILE", "").strip()
    try:
        path = Path(configured)
        if not configured or path.is_symlink() or path.stat().st_mode & 0o077:
            raise ValueError
        raw = path.read_bytes()
        if len(raw) > 1024 * 1024:
            raise ValueError
        data = json.loads(raw)
        if data.get("schema_version") != 1:
            raise ValueError
        for binding in data["bindings"]:
            if binding["local_agent_id"] == agent_id and binding["context_id"] == context_id:
                if not all(_OPAQUE.fullmatch(binding[field]) for field in ["application", "workspace_id", "agent_id"]):
                    raise ValueError
                if not binding.get("workload_key") or binding.get("capability_version") != "gorouter-workload-usage-v1":
                    raise ValueError
                return dict(binding)
    except (OSError, ValueError, TypeError, KeyError) as error:
        raise AccountingUnavailable("Agent accounting binding unavailable") from error
    raise AccountingUnavailable("Agent accounting binding unavailable")


def current_accounting():
    return _CURRENT.get()


@contextmanager
def inference_accounting(binding, conversation_id="", run_id="", parent_run_id=""):
    headers = {}
    for name, value in [("Conversation", conversation_id), ("Run", run_id), ("Parent-Run", parent_run_id)]:
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
