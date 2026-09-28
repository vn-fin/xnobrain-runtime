"""Native Agent Maker tools bound by the host to one authenticated chat run."""

import asyncio
import copy
import json
import threading
from concurrent.futures import CancelledError
from contextlib import contextmanager

from pydantic import ValidationError

from ..models.agent_maker import MakerBuild, MakerInspect, MakerPrepare
from ..repositories.base import StoreError
from ..services.base import ServiceError
from ..trusted_context import TrustedRequestContext

_LOCK = threading.RLock()
_BINDINGS = {}
_SCHEMAS = {}


def call(name, args, **kwargs):
    session = str(kwargs.get("session_id") or "")
    with _LOCK:
        binding = _BINDINGS.get(session)
        if binding is None:
            return json.dumps(
                {"success": False, "error": {"code": "agent_maker_authority_required"}}
            )
        service, agent, run_id, trusted, loop, pending = binding
        # Native handlers run in the engine's tool threads. Certification stays
        # on the Runtime loop so cancellation and shared async clients keep working.
        future = asyncio.run_coroutine_threadsafe(
            service.execute(
                name,
                args,
                agent=agent,
                session=session,
                run_id=run_id,
                trusted=trusted,
            ),
            loop,
        )
        pending.add(future)
    try:
        return json.dumps({"success": True, "data": future.result()}, ensure_ascii=False)
    except ValidationError as error:
        return json.dumps(
            {
                "success": False,
                "error": {
                    "code": "invalid_agent_maker_request",
                    "fields": [list(item["loc"]) for item in error.errors()],
                },
            }
        )
    except (StoreError, ServiceError) as error:
        return json.dumps({"success": False, "error": {"code": error.code, "message": str(error)}})
    except CancelledError:
        return json.dumps({"success": False, "error": {"code": "agent_maker_run_inactive"}})
    except Exception:
        # Engine dispatch logs uncaught exception text; never expose payloads there.
        return json.dumps({"success": False, "error": {"code": "agent_maker_operation_failed"}})
    finally:
        with _LOCK:
            pending.discard(future)


def register_tools():
    from tools.registry import registry

    with _LOCK:
        if _SCHEMAS:
            return
        for name, description, model in (
            (
                "agent_maker_inspect",
                "Inspect Agent Maker schemas and blueprints in this chat's work context. Use when asked to create an agent.",
                MakerInspect,
            ),
            (
                "agent_maker_prepare",
                "Save a complete specialist blueprint for a user-requested agent. Identity and destination are supplied by the platform. Returns the exact revision and digest for agent_maker_build. Does not execute the child.",
                MakerPrepare,
            ),
            (
                "agent_maker_build",
                "Create the user-requested agent: automatically accept the exact blueprint, scaffold, certify with synthetic job/refusal/tool/context cases, then activate only if all pass. No manual approval click is needed. Reuse the same key for a network retry; use a new key only when the user requests another failed certification attempt. Stop and report failed cases. Never use CLI profile creation as a substitute.",
                MakerBuild,
            ),
        ):
            function = {
                "name": name,
                "description": description,
                "parameters": model.model_json_schema(),
            }
            registry.register(
                name=name,
                toolset="xnobrain_agent_maker",
                schema=function,
                handler=lambda args, _name=name, **kw: call(_name, args, **kw),
                check_fn=lambda: False,
            )
            _SCHEMAS[name] = {"type": "function", "function": function}


@contextmanager
def bind_run(service, agent, session, run_id, trusted):
    if not isinstance(trusted, TrustedRequestContext) or not trusted.subject:
        yield False
        return
    service.authority(agent, session, run_id, trusted)
    register_tools()
    with _LOCK:
        if session in _BINDINGS:
            raise ServiceError("Session already bound", status=409, code="agent_maker_session_busy")
        pending = set()
        _BINDINGS[session] = (service, agent, run_id, trusted, asyncio.get_running_loop(), pending)
    try:
        yield True
    finally:
        with _LOCK:
            _BINDINGS.pop(session, None)
            for future in list(pending):
                future.cancel()


def install_tools(agent, session):
    with _LOCK:
        if session not in _BINDINGS or int(getattr(agent, "_delegate_depth", 0) or 0):
            return
        schemas = copy.deepcopy(list(_SCHEMAS.values()))
    existing = {tool.get("function", {}).get("name") for tool in getattr(agent, "tools", [])}
    agent.tools = list(getattr(agent, "tools", []) or []) + [
        tool for tool in schemas if tool["function"]["name"] not in existing
    ]
    agent.valid_tool_names = set(getattr(agent, "valid_tool_names", set())) | set(_SCHEMAS)
