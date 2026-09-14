"""Engine-native tools bound to a verified parent session, never model identity."""

from __future__ import annotations

import copy
import json
import threading
from contextlib import contextmanager

from ..models.custom_page import PreparePage, QueryPage, WriteRecords
from ..repositories.base import StoreError
from ..services.base import ServiceError
from ..trusted_context import TrustedRequestContext

_LOCK = threading.RLock()
_BINDINGS = {}
_SCHEMAS = {}


def _call(name, args, **kwargs):
    session_id = str(kwargs.get("session_id") or "")
    with _LOCK:
        binding = _BINDINGS.get(session_id)
    if binding is None:
        return json.dumps({"error": {"code": "custom_page_run_authority_required"}})
    service, agent_id, trusted, run_id = binding
    try:
        with service.repository.lock():
            # A transient binding is not authority by itself. Recheck the durable run
            # and immutable context at each read/write, so terminal/archived work stops.
            record = service.platform.repository.get_conversation_run(agent_id, session_id, run_id)
            if record["status"] not in {"queued", "running"} or (
                record.get("cancellation") or {}
            ).get("requested"):
                raise StoreError(
                    "run is no longer active", status=403, code="custom_page_run_inactive"
                )
            from ..services.conversation_authority import require_run

            require_run(
                service.platform.repository, agent_id, session_id, record, trusted, personal=True
            )
            context = record.get("ownership_context") or {}
            if (
                context.get("owner_kind") != "personal"
                or context.get("state", "active") != "active"
            ):
                raise StoreError(
                    "Personal context required", status=403, code="custom_page_personal_required"
                )
            schedule_id = record.get("custom_page_schedule")
            if schedule_id:
                binding = service.schedules.repository.get(
                    agent_id, service.authority(agent_id, trusted), schedule_id
                )
                if binding["state"] != "running" or binding["run_id"] != run_id:
                    raise ValueError("schedule is no longer permitted")
            allowed = record.get("custom_page_datasets")
            if allowed is not None:
                page = service.read(agent_id, trusted)
                revision = record.get("custom_page_revision")
                if page["status"] != "active" or page["active"] != revision:
                    raise ValueError("action page revision is no longer active")
                if name == "custom_page_write" and args.get("expected_revision") != revision:
                    raise ValueError("write revision is outside action scope")
                if name == "custom_page_query":
                    args = {**args, "expected_revision": revision}
                if name == "custom_page_prepare":
                    raise ValueError("action cannot edit page schema")
                if name == "custom_page_write" and args.get("dataset_id") not in allowed:
                    raise ValueError("dataset is outside action scope")
                if name == "custom_page_query":
                    page = service.read(agent_id, trusted)
                    query = next(
                        (
                            q
                            for q in page["page"]["manifest"]["queries"]
                            if q["id"] == args.get("query_id")
                        ),
                        None,
                    )
                    if (
                        query is None
                        or query["dataset"] not in allowed
                        or args.get("draft_revision")
                    ):
                        raise ValueError("query is outside action scope")
            if name == "custom_page_inspect":
                if args:
                    raise ValueError("unexpected input")
                result = {
                    "capabilities": service.capabilities(agent_id, trusted),
                    "prepare_schema": PreparePage.model_json_schema(),
                }
                try:
                    result["page"] = service.read(agent_id, trusted)
                except StoreError as error:
                    if error.code != "custom_page_not_found":
                        raise
                    result["page"] = None
            elif name == "custom_page_prepare":
                result = service.prepare(agent_id, args, trusted)
                result = {
                    **result,
                    "preview_path": f"/agents/{agent_id}/custom?draft={result['revision']}",
                    "approval_required": True,
                }
            elif name == "custom_page_query":
                data = copy.deepcopy(args)
                query_id = data.pop("query_id", "")
                result = service.query(agent_id, query_id, data, trusted)
            elif name == "custom_page_write":
                data = copy.deepcopy(args)
                dataset = data.pop("dataset_id", "")
                # Run attribution is supplied by the host, not the model.
                for row in data.get("records", []):
                    row.setdefault("provenance", {})["run_id"] = run_id
                result = service.write(agent_id, dataset, data, trusted)
            else:
                raise ValueError("unsupported tool")
            return json.dumps({"success": True, "data": result}, ensure_ascii=True)
    except (StoreError, ServiceError, ValueError, TypeError, KeyError, AttributeError):
        return json.dumps({"success": False, "error": {"code": "custom_page_operation_denied"}})


def register_tools():
    from tools.registry import registry

    with _LOCK:
        if _SCHEMAS:
            return
        tools = [
            (
                "custom_page_inspect",
                "Inspect this agent's private app schema/catalog and current page; no paid work or activation.",
                {"type": "object", "properties": {}, "additionalProperties": False},
            ),
            (
                "custom_page_prepare",
                "Prepare a validated declarative page draft after an explicit request. Returns a preview link. Never activates, schedules or grants permissions.",
                PreparePage.model_json_schema(),
            ),
            (
                "custom_page_query",
                "Read stored results using a registered query ID. No LLM or collection.",
                QueryPage.model_json_schema(),
            ),
            (
                "custom_page_write",
                "Write authorized collected/generated data for this agent's app. Use stable source IDs and idempotency; preserve truthful provenance. Requires the active schema, or a validated initial draft before first activation.",
                WriteRecords.model_json_schema(),
            ),
        ]
        for name, description, schema in tools:
            if name in {"custom_page_query", "custom_page_write"}:
                field = "query_id" if name.endswith("query") else "dataset_id"
                schema["properties"][field] = {
                    "type": "string",
                    "pattern": "^[a-z][a-z0-9_]{0,47}$",
                }
                schema.setdefault("required", []).append(field)
            function = {"name": name, "description": description, "parameters": schema}
            registry.register(
                name=name,
                toolset="xnobrain_custom_page",
                schema=function,
                handler=lambda args, _name=name, **kw: _call(_name, args, **kw),
                check_fn=lambda: False,
            )
            # Tools are injected only into the verified originating agent below;
            # they are unavailable to global discovery/child agents.
            _SCHEMAS[name] = {"type": "function", "function": function}


@contextmanager
def bind_run(service, agent_id, session_id, run_id, trusted):
    if not isinstance(trusted, TrustedRequestContext) or not trusted.subject:
        yield False
        return
    service.authority(agent_id, trusted)
    from ..services.conversation_authority import require_run

    record = service.platform.repository.get_conversation_run(agent_id, session_id, run_id)
    require_run(service.platform.repository, agent_id, session_id, record, trusted, personal=True)
    register_tools()
    with _LOCK:
        if session_id in _BINDINGS:
            raise StoreError("session already bound", status=409, code="custom_page_session_busy")
        _BINDINGS[session_id] = (service, agent_id, trusted, run_id)
    try:
        yield True
    finally:
        with _LOCK:
            _BINDINGS.pop(session_id, None)


def install_tools(agent, session_id):
    with _LOCK:
        if session_id not in _BINDINGS or int(getattr(agent, "_delegate_depth", 0) or 0) != 0:
            return
        service, agent_id, _, run_id = _BINDINGS[session_id]
        schemas = copy.deepcopy(list(_SCHEMAS.values()))
    existing = {t.get("function", {}).get("name") for t in getattr(agent, "tools", [])}
    agent.tools = list(getattr(agent, "tools", []) or []) + [
        t for t in schemas if t["function"]["name"] not in existing
    ]
    agent.valid_tool_names = set(getattr(agent, "valid_tool_names", set())) | set(_SCHEMAS)

    record = service.platform.repository.get_conversation_run(agent_id, session_id, run_id)
    if record.get("custom_page_datasets") is not None:
        # Scoped actions cannot bypass their data API through shell/file/code tools
        # or create unapproved child work. Collection retains established web tools.
        from agent.iteration_budget import IterationBudget

        allowed = {
            "custom_page_inspect",
            "custom_page_query",
            "custom_page_write",
            "web_search",
            "web_extract",
        }
        agent.tools = [
            tool for tool in agent.tools if tool.get("function", {}).get("name") in allowed
        ]
        agent.valid_tool_names &= allowed
        agent.max_iterations = 20
        agent.iteration_budget = IterationBudget(20)
