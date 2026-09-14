"""Only a host-bound parent may inspect/propose a declarative layout, never apply."""

import copy
import json
import threading
from contextlib import contextmanager

from ..models.ui_composition import ProposeLayout
from ..repositories.ui_composition import fail
from ..services.base import ServiceError
from ..services.ui_composition import ACCENT_OPTIONS, INSPECTOR_POSITIONS, NAVIGATION_OPTIONS
from ..trusted_context import TrustedRequestContext

_BINDINGS = {}
_LOCK = threading.RLock()
_SCHEMAS = {}


def call(name, args, **kwargs):
    session = str(kwargs.get("session_id") or "")
    with _LOCK:
        binding = _BINDINGS.get(session)
    if not binding:
        return json.dumps({"error": {"code": "ui_assistance_authority_required"}})
    service, agent, run_id, scope = binding
    try:
        run = service.platform.repository.get_conversation_run(agent, session, run_id)
        if run["status"] not in {"running", "queued"} or run.get("ui_assistance") != scope:
            fail()
        row = service.repository.get(scope["owner"], scope["id"])
        if (
            row["cancelled"]
            or row["request"]["agent_id"] != agent
            or row["request"]["conversation_id"] != session
        ):
            fail()
        tenant, subject = scope["owner"].split("\0", 1)
        trusted = TrustedRequestContext(subject, tenant)
        service.context(row["request"], trusted)
        from ..services.conversation_authority import require_run

        require_run(service.platform.repository, agent, session, run, trusted)
        if name == "ui_layout_catalog":
            if args:
                fail()
            result = {
                key: row["request"][key]
                for key in ("layout", "catalog", "layout_id", "base_revision", "context")
            }
            result["inspector_positions"] = list(INSPECTOR_POSITIONS)
            result["navigation_options"] = list(NAVIGATION_OPTIONS)
            result["default_page_options"] = list(NAVIGATION_OPTIONS)
            result["accent_options"] = list(ACCENT_OPTIONS)
            result["proposal_schema"] = ProposeLayout.model_json_schema()
        elif name == "ui_layout_propose":
            body = ProposeLayout.model_validate(args)
            service.validate_layout(body.layout, row["request"]["catalog"])
            service.repository.change(scope["owner"], scope["id"], result=body.layout)
            result = {
                "staged": True,
                "assistance_id": scope["id"],
                "approval_required": True,
                "message": "Return to Appearance & layout to review this exact proposal. It has not been applied.",
            }
        else:
            fail()
        return json.dumps({"success": True, "data": result})
    except (ValueError, TypeError, KeyError, AttributeError, ServiceError):
        return json.dumps({"success": False, "error": {"code": "ui_assistance_operation_denied"}})


def register_tools():
    from tools.registry import registry

    with _LOCK:
        if _SCHEMAS:
            return
        for name, description, parameters in (
            (
                "ui_layout_catalog",
                "Inspect the Control-approved catalog and current layout bound to this request. No data reads or spending.",
                {"type": "object", "properties": {}, "additionalProperties": False},
            ),
            (
                "ui_layout_propose",
                "Stage a bounded declarative layout matching the approved catalog. Never applies, installs or edits source.",
                ProposeLayout.model_json_schema(),
            ),
        ):
            function = {"name": name, "description": description, "parameters": parameters}
            registry.register(
                name=name,
                toolset="xnobrain_ui_layout",
                schema=function,
                handler=lambda args, _name=name, **kw: call(_name, args, **kw),
                check_fn=lambda: False,
            )
            _SCHEMAS[name] = {"type": "function", "function": function}


@contextmanager
def bind_run(service, agent, session, run_id, scope):
    if not scope:
        yield
        return
    register_tools()
    with _LOCK:
        if session in _BINDINGS:
            fail("ui_assistance_session_busy")
        _BINDINGS[session] = (service, agent, run_id, scope)
    try:
        yield
    finally:
        with _LOCK:
            _BINDINGS.pop(session, None)


def install_tools(agent, session):
    with _LOCK:
        if session not in _BINDINGS:
            return
        if int(getattr(agent, "_delegate_depth", 0) or 0):
            fail("ui_assistance_child_denied", 403)
        agent.tools = copy.deepcopy(list(_SCHEMAS.values()))
        agent.valid_tool_names = set(_SCHEMAS)
    from agent.iteration_budget import IterationBudget

    agent.max_iterations = 10
    agent.iteration_budget = IterationBudget(10)
