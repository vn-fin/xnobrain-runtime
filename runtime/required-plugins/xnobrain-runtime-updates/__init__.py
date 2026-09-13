"""Narrow Big Brother update tools backed only by typed Control HTTP APIs."""

from __future__ import annotations

import json
import os
from urllib.parse import quote

import httpx

TOOLSET = "xnobrain_runtime_updates"
BASE_PATH = "/xnobrain/api/control/v1/workspace/current"
TIMEOUT = httpx.Timeout(15.0, connect=5.0)


def _available() -> bool:
    return bool(
        os.getenv("RUNTIME_CONTROL_URL", "").strip()
        and os.getenv("RUNTIME_WORKSPACE_UPDATE_TOKEN_FILE", "").strip()
    )


def _token() -> str:
    path = os.getenv("RUNTIME_WORKSPACE_UPDATE_TOKEN_FILE", "").strip()
    if not path:
        raise RuntimeError("workspace update service identity is unavailable")
    with open(path, encoding="utf-8") as source:
        token = source.read().strip()
    if not token:
        raise RuntimeError("workspace update service identity is unavailable")
    return token


async def _call(method: str, path: str, payload: dict | None = None) -> str:
    endpoint = os.getenv("RUNTIME_CONTROL_URL", "").strip().rstrip("/")
    if not endpoint:
        return json.dumps({"success": False, "error": {"code": "updates_unavailable"}})
    try:
        async with httpx.AsyncClient(
            timeout=TIMEOUT,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            response = await client.request(
                method,
                endpoint + path,
                headers={"Authorization": "Bearer " + _token()},
                json=payload,
            )
        body = response.json()
        if not isinstance(body, dict):
            raise ValueError("invalid response")
        return json.dumps(body, ensure_ascii=False)
    except (OSError, httpx.HTTPError, ValueError, RuntimeError):
        return json.dumps({"success": False, "error": {"code": "updates_unavailable"}})


async def workspace_update_check(_args: dict, **_kwargs) -> str:
    return await _call("GET", BASE_PATH + "/updates")


async def workspace_update_plan(args: dict, **_kwargs) -> str:
    selector = {"type": str(args.get("selector_type") or "")}
    if selector["type"] == "release_tag":
        selector["tag"] = str(args.get("tag") or "")
    return await _call("POST", BASE_PATH + "/update-plans", {"selector": selector})


async def workspace_update_request(args: dict, **_kwargs) -> str:
    return await _call(
        "POST",
        BASE_PATH + "/updates",
        {
            "plan_id": str(args.get("plan_id") or ""),
            "plan_hash": str(args.get("plan_hash") or ""),
            "approval_id": str(args.get("approval_id") or ""),
            "idempotency_key": str(args.get("idempotency_key") or ""),
        },
    )


async def workspace_update_status(args: dict, **_kwargs) -> str:
    operation_id = quote(str(args.get("operation_id") or ""), safe="")
    return await _call("GET", BASE_PATH + "/updates/" + operation_id)


SELECTOR_PROPERTIES = {
    "selector_type": {
        "type": "string",
        "enum": ["release_tag", "release_branch_latest"],
        "description": "Choose a trusted release tag or the exact current release-branch head.",
    },
    "tag": {
        "type": "string",
        "pattern": "^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$",
        "description": "Required only with release_tag.",
    },
}


def _schema(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": properties,
            "required": required,
        },
    }


def register(ctx) -> None:
    tools = (
        (
            "workspace_update_check",
            "Check installed Runtime identity, compatible updates, storage layout, and blockers.",
            {},
            [],
            workspace_update_check,
        ),
        (
            "workspace_update_plan",
            "Prepare a non-authorizing update plan from a trusted tag or the release branch.",
            SELECTOR_PROPERTIES,
            ["selector_type"],
            workspace_update_plan,
        ),
        (
            "workspace_update_request",
            "Request the exact approved plan. approval_id must come from the trusted approval service; this tool cannot approve downtime.",
            {
                "plan_id": {"type": "string"},
                "plan_hash": {"type": "string"},
                "approval_id": {"type": "string"},
                "idempotency_key": {"type": "string"},
            },
            ["plan_id", "plan_hash", "approval_id", "idempotency_key"],
            workspace_update_request,
        ),
        (
            "workspace_update_status",
            "Read durable progress and forward-only recovery actions for one update operation.",
            {"operation_id": {"type": "string"}},
            ["operation_id"],
            workspace_update_status,
        ),
    )
    for name, description, properties, required, handler in tools:
        ctx.register_tool(
            name=name,
            toolset=TOOLSET,
            schema=_schema(name, description, properties, required),
            handler=handler,
            check_fn=_available,
            is_async=True,
            emoji="⬆️",
        )
