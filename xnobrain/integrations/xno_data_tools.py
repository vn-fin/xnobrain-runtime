"""Market data tools bound to one interactive chat capability, never a JWT."""

import asyncio
import copy
import json
import os
import threading
from concurrent.futures import CancelledError
from contextlib import contextmanager

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..feature_flags import enabled


class DataRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sql: str = Field(min_length=1, max_length=20000)
    limit: int | None = Field(default=None, ge=1, le=1000)


_LOCK = threading.RLock()
_BINDINGS = {}
_SCHEMA = {}
_PATH = "/xnobrain/api/control/internal/v1/xno-data"


def _error(code):
    return json.dumps({"success": False, "error": {"code": code}})


async def _execute(grant, session, run, args):
    destination = os.getenv("RUNTIME_CONTROL_URL", "").rstrip("/")
    if not destination:
        return _error("xno_data_unavailable")
    payload = {**args, "session_id": session, "run_id": run}
    try:
        async with httpx.AsyncClient(timeout=35, trust_env=False, follow_redirects=False) as client:
            async with client.stream(
                "POST",
                destination + _PATH,
                headers={"Authorization": "Bearer " + grant},
                json=payload,
            ) as response:
                # Error bodies are intentionally not exposed to the model.
                if response.status_code != 200:
                    code = {
                        400: "xno_data_invalid_query",
                        401: "xno_data_auth_expired",
                        403: "xno_data_forbidden",
                        404: "xno_data_resource_not_found",
                        408: "xno_data_query_timeout",
                        429: "xno_data_rate_limited",
                    }.get(response.status_code, "xno_data_unavailable")
                    return _error(code)
                chunks = bytearray()
                async for chunk in response.aiter_bytes():
                    chunks.extend(chunk)
                    if len(chunks) > 4 * 1024 * 1024 + 4096:
                        return _error("xno_data_response_too_large")
                if grant.encode() in chunks:
                    return _error("xno_data_unavailable")
                envelope = json.loads(chunks)
                if not isinstance(envelope, dict) or envelope.get("success") is not True:
                    return _error("xno_data_unavailable")
                return json.dumps(
                    {"success": True, "data": envelope.get("data")}, ensure_ascii=False
                )
    except (httpx.HTTPError, ValueError, TypeError):
        return _error("xno_data_unavailable")


def call(args, **kwargs):
    try:
        request = DataRequest.model_validate(args)
    except ValidationError:
        return _error("xno_data_invalid_request")
    session = str(kwargs.get("session_id") or "")
    with _LOCK:
        binding = _BINDINGS.get(session)
        if binding is None:
            return _error("xno_data_authority_required")
        grant, run, loop, pending = binding
        future = asyncio.run_coroutine_threadsafe(
            _execute(grant, session, run, request.model_dump(exclude_none=True)), loop
        )
        pending.add(future)
    try:
        return future.result(timeout=40)
    except CancelledError:
        return _error("xno_data_run_inactive")
    except Exception:
        future.cancel()
        return _error("xno_data_unavailable")
    finally:
        with _LOCK:
            pending.discard(future)


def register_tools():
    from tools.registry import registry

    with _LOCK:
        if _SCHEMA:
            return
        function = {
            "name": "xno_data",
            "description": (
                "Query XNO read-only SQL as this chat user. Discover schemas and tables "
                "through information_schema, inspect columns before reading each table, "
                "select only necessary named columns (no SELECT *), and use fixed "
                "timestamp literals with >= for time bounds. Results are CSV; limit "
                "defaults to 100, maximum 1000. has_more means narrow filters/time ranges. "
                "Authentication is platform-managed; never request or pass credentials."
            ),
            "parameters": DataRequest.model_json_schema(),
        }
        registry.register(
            name="xno_data",
            toolset="xnobrain_xno_data",
            schema=function,
            handler=call,
            check_fn=lambda: False,
        )
        _SCHEMA.update({"type": "function", "function": function})


@contextmanager
def bind_run(session, run, grant):
    if not enabled("XNO_DATA") or not grant or not os.getenv("RUNTIME_CONTROL_URL"):
        yield False
        return
    register_tools()
    with _LOCK:
        if session in _BINDINGS:
            raise ValueError("xno_data_session_busy")
        pending = set()
        _BINDINGS[session] = (grant, run, asyncio.get_running_loop(), pending)
    try:
        yield True
    finally:
        with _LOCK:
            _BINDINGS.pop(session, None)
            for future in list(pending):
                future.cancel()


async def release_grant(session, run, grant):
    destination = os.getenv("RUNTIME_CONTROL_URL", "").rstrip("/")
    if not grant or not destination:
        return
    try:
        async with httpx.AsyncClient(timeout=2, trust_env=False, follow_redirects=False) as client:
            await client.post(
                destination + _PATH + "/release",
                headers={"Authorization": "Bearer " + grant},
                json={"session_id": session, "run_id": run},
            )
    except httpx.HTTPError:
        # Control expires and purges grants after one hour if cleanup is interrupted.
        pass


def install_tools(agent, session):
    with _LOCK:
        if session not in _BINDINGS or int(getattr(agent, "_delegate_depth", 0) or 0):
            return
        schema = copy.deepcopy(_SCHEMA)
    existing = {tool.get("function", {}).get("name") for tool in getattr(agent, "tools", [])}
    if "xno_data" not in existing:
        agent.tools = list(getattr(agent, "tools", []) or []) + [schema]
    agent.valid_tool_names = set(getattr(agent, "valid_tool_names", set())) | {"xno_data"}
