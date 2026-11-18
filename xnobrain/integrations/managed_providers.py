"""Generic managed native provider registration from an installed definition.

Control owns provider tool names, schemas, modes, resources, operation enums and
skill/reference data. Runtime only reads the reviewed ``provider.json`` shipped
inside the installed package and dispatches calls to Control's managed provider
endpoint. No provider identifier, resource list or operation catalog is
embedded here.
"""

import json
import os
from pathlib import Path
from uuid import UUID

import httpx

from ..defaults import BIG_BROTHER_AGENT_ID
from . import control_grants, extension_policy
from .managed_mcp import _profile_scope
from .native_provider_packages import load_installed_definition

SAFE_ERRORS = {
    "provider_record_not_found",
    "provider_conflict",
    "provider_partial_failure",
    "provider_write_outcome_unknown",
    "provider_invalid_request",
    "provider_auth_failed",
    "provider_forbidden",
    "provider_rate_limited",
    "provider_unavailable",
    "mcp_grant_invalid",
    "mcp_connection_not_found",
    "mcp_credentials_unavailable",
    "mcp_needs_sign_in",
}


def failure(code):
    return json.dumps({"success": False, "error": {"code": code}})


def _profile_mismatch(profile):
    from hermes_constants import get_hermes_home

    return Path(get_hermes_home()).resolve() != profile.resolve()


def _argument_error(schema, args):
    """Validate only the exact schema that was installed and approved."""
    if not isinstance(schema, dict) or not isinstance(args, dict):
        return True
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return True
    if set(args) - set(properties):
        return True
    required = schema.get("required") or []
    if any(item not in args for item in required):
        return True
    for name, definition in properties.items():
        if name not in args or not isinstance(definition, dict):
            continue
        allowed = definition.get("enum")
        if isinstance(allowed, list) and args[name] not in allowed:
            return True
    return False


def _endpoint(tool, args):
    request = tool.get("request")
    hint = request.get("endpoint") if isinstance(request, dict) else None
    if hint == "read":
        return "read"
    if hint == "execute":
        return "execute"
    # Control's operation-backed tools always expose an ``operation`` property;
    # legacy page/resource-only reads stay on the read endpoint.
    return "execute" if "operation" in args else "read"


def execute_definition(definition, tool, args, profile):
    """Execute one installed definition with the captured, active profile."""
    if _profile_mismatch(profile):
        return failure("provider_profile_mismatch")
    if _argument_error(tool.get("input_schema"), args):
        return failure("provider_invalid_request")
    provider = definition["id"]
    mode = tool["mode"]
    endpoint = _endpoint(tool, args)
    dispatched = False
    try:
        config = extension_policy.read_config(profile).get("plugins") or {}
        if provider not in config.get("enabled", []) or provider in config.get("disabled", []):
            return failure("provider_plugin_disabled")
        extension_policy.validate_profile_extensions(profile)
        state = extension_policy.load_state(profile)
        connection = state.get("provider_connections", {}).get(provider, {}).get("connection_id")
        connection = str(UUID(connection))
        grant = control_grants.get(_profile_scope(profile))
        base = os.environ.get("RUNTIME_CONTROL_URL", "").rstrip("/")
        if not grant or not base:
            return failure("provider_connection_unavailable")
        # The root profile can be named "root" or ".hermes" on legacy installs;
        # Control stores its stable public ID, not the directory basename.
        # HERMES_HOME is turn-local, so it must not identify the root profile.
        root_profile = Path(
            os.environ.get("HERMES_ROOT_PROFILE") or Path.home() / ".hermes"
        ).resolve()
        agent_id = BIG_BROTHER_AGENT_ID if profile.resolve() == root_profile else profile.name
        payload = {**args, "provider": provider, "agent_id": agent_id}
        if endpoint == "execute":
            payload["mode"] = mode
        if len(json.dumps(payload).encode()) > 300 * 1024:
            return failure("provider_invalid_request")
        with httpx.Client(timeout=35, follow_redirects=False, trust_env=False) as client:
            dispatched = True
            with client.stream(
                "POST",
                f"{base}/xnobrain/api/control/internal/v1/providers/{connection}/{endpoint}",
                headers={"Authorization": f"Bearer {grant}"},
                json=payload,
            ) as response:
                raw = bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 2_100_000:
                        return failure(
                            "provider_write_outcome_unknown"
                            if mode == "write"
                            else "provider_response_too_large"
                        )
                result = json.loads(raw)
                if (
                    response.status_code != 200
                    or result.get("success") is not True
                    or result.get("data") is None
                ):
                    data = result.get("data")
                    code = data.get("code") if isinstance(data, dict) else None
                    return failure(
                        code
                        if code in SAFE_ERRORS
                        else (
                            "provider_write_outcome_unknown"
                            if mode == "write"
                            else "provider_unavailable"
                        )
                    )
                return json.dumps(
                    {
                        "success": True,
                        "provider": provider,
                        "page": args.get("page", 1),
                        "coverage": ("operation" if endpoint == "execute" else "single_page"),
                        **({"operation": args.get("operation")} if endpoint == "execute" else {}),
                        "data": result.get("data"),
                    }
                )
    except httpx.HTTPError:
        return failure(
            "provider_write_outcome_unknown"
            if mode == "write"
            else "provider_connection_unavailable"
        )
    except (OSError, ValueError, TypeError, AttributeError):
        return failure(
            "provider_write_outcome_unknown"
            if mode == "write" and dispatched
            else "provider_connection_unavailable"
        )
    except Exception:
        # Approval and scope guards fail closed; never expose paths or tool context.
        return failure("provider_connection_unavailable")


def register(ctx, definition_path):
    """Register tool names, schemas and modes from the installed definition."""
    from hermes_constants import get_hermes_home

    if not isinstance(definition_path, Path):
        raise ValueError("managed native provider register requires provider.json")
    definition = load_installed_definition(definition_path)
    profile = Path(get_hermes_home()).resolve()
    for tool in definition["tools"]:
        ctx.register_tool(
            name=tool["name"],
            toolset=definition["id"],
            schema={
                "name": tool["name"],
                "description": tool["description"],
                "parameters": tool["input_schema"],
            },
            handler=lambda args, _tool=tool, **kwargs: execute_definition(
                definition, _tool, args, profile
            ),
        )
    skill = definition["skill"]
    ctx.register_skill(
        skill["name"],
        definition_path.parent / skill["path"],
        description=skill["description"],
    )
