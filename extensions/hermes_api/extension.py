"""Minimal local routes for the sandbox Hermes gateway.

The VM keeps one root gateway process for authenticated internal HTTP calls.
Named agents are not loaded into that process; each chat request starts Hermes
with the target agent profile as ``HERMES_HOME`` and the agent workspace as cwd.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import logging
from typing import Any

from gateway.platforms import api_server as upstream

from .agents import AgentAPIError, AgentManager
from .global_config import ConfigAPIError, GlobalConfigManager
from .nine_router import NineRouterAPIError, NineRouterManager


logger = logging.getLogger(__name__)


def _api_write_approval_callback(
    command: str,
    description: str,
    *,
    allow_permanent: bool = False,
) -> str:
    """Route foreground memory-write approval through the Runs API queue."""
    try:
        from tools import approval

        session_key = str(approval.get_current_session_key() or "").strip()
        with approval._lock:
            notify = approval._gateway_notify_cbs.get(session_key)
        if not session_key or notify is None:
            return ""

        decision = approval._await_gateway_decision(
            session_key,
            notify,
            {
                "command": command,
                "description": description,
                "pattern_key": "memory_write",
                "pattern_keys": ["memory_write"],
                "allow_permanent": allow_permanent,
            },
            surface="api_server",
        )
        return str(decision.get("choice") or "") if decision.get("resolved") else ""
    except Exception:
        logger.exception("API write approval bridge failed")
        return ""


class ExtendedAPIServerAdapter(upstream.APIServerAdapter):
    """Upstream adapter plus the local named-agent management routes."""

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._custom_agent_manager: AgentManager | None = None
        self._custom_config_manager: GlobalConfigManager | None = None
        self._custom_nine_router_manager: NineRouterManager | None = None
        self._custom_routes_registered = False

    def _agents(self) -> AgentManager:
        if self._custom_agent_manager is None:
            self._custom_agent_manager = AgentManager()
        return self._custom_agent_manager

    def _config(self) -> GlobalConfigManager:
        if self._custom_config_manager is None:
            self._custom_config_manager = GlobalConfigManager()
        return self._custom_config_manager

    def _router(self) -> NineRouterManager:
        if self._custom_nine_router_manager is None:
            self._custom_nine_router_manager = NineRouterManager()
        return self._custom_nine_router_manager

    def _create_agent(self, *args: Any, **kwargs: Any):
        """Create an API agent that resumes the named Hermes session.

        Upstream's Sessions API loads ``state.db`` history before a turn, but
        the Runs API only uses an explicitly supplied ``conversation_history``.
        The web client needs Runs for approvals and stopping, so hydrate an
        otherwise empty history from the same profile session used by the CLI.
        """
        agent = super()._create_agent(*args, **kwargs)

        session_id = kwargs.get("session_id")
        if not session_id:
            try:
                bound = inspect.signature(super()._create_agent).bind_partial(*args, **kwargs)
                session_id = bound.arguments.get("session_id")
            except (TypeError, ValueError):
                session_id = None
        session_id = str(session_id or "").strip()
        run_conversation = getattr(agent, "run_conversation", None)
        if not session_id or not callable(run_conversation):
            return agent

        try:
            owner_loop = asyncio.get_running_loop()
        except RuntimeError:
            owner_loop = None

        def load_session_history():
            history = self._conversation_history_for_session(session_id)
            if not inspect.isawaitable(history):
                return history
            if owner_loop is not None and owner_loop.is_running():
                return asyncio.run_coroutine_threadsafe(history, owner_loop).result()
            return asyncio.run(history)

        @functools.wraps(run_conversation)
        def run_conversation_with_session_history(*run_args: Any, **run_kwargs: Any):
            hydrated_args = list(run_args)
            if len(hydrated_args) > 1:
                if not hydrated_args[1]:
                    hydrated_args[1] = load_session_history()
            elif not run_kwargs.get("conversation_history"):
                run_kwargs["conversation_history"] = load_session_history()

            try:
                from tools.terminal_tool import _get_approval_callback, set_approval_callback
            except Exception:
                return run_conversation(*hydrated_args, **run_kwargs)

            previous_approval_callback = _get_approval_callback()
            set_approval_callback(_api_write_approval_callback)
            try:
                return run_conversation(*hydrated_args, **run_kwargs)
            finally:
                set_approval_callback(previous_approval_callback)

        agent.run_conversation = run_conversation_with_session_history
        return agent

    def _agent_error(self, exc: AgentAPIError):
        return upstream.web.json_response(
            upstream._openai_error(str(exc), code=exc.code),
            status=exc.status,
        )

    def _config_error(self, exc: ConfigAPIError):
        return upstream.web.json_response(
            upstream._openai_error(str(exc), code=exc.code),
            status=exc.status,
        )

    def _router_error(self, exc: NineRouterAPIError):
        return upstream.web.json_response(
            upstream._openai_error(str(exc), code=exc.code),
            status=exc.status,
        )

    async def _handle_router_status(self, request):
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        return upstream.web.json_response(await self._router().status())

    async def _handle_list_router_providers(self, request):
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        try:
            return upstream.web.json_response(await self._router().list_connections())
        except NineRouterAPIError as exc:
            return self._router_error(exc)

    async def _handle_create_router_provider(self, request):
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        body, err = await self._read_json_body(request)
        if err:
            return err
        try:
            payload = await self._router().create_api_key_connection(body)
            return upstream.web.json_response(payload, status=201)
        except NineRouterAPIError as exc:
            return self._router_error(exc)

    async def _handle_delete_router_provider(self, request):
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        try:
            return upstream.web.json_response(
                await self._router().delete_connection(request.match_info["connection_id"])
            )
        except NineRouterAPIError as exc:
            return self._router_error(exc)

    async def _handle_test_router_provider(self, request):
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        try:
            return upstream.web.json_response(
                await self._router().test_connection(request.match_info["connection_id"])
            )
        except NineRouterAPIError as exc:
            return self._router_error(exc)

    async def _handle_router_oauth(self, request):
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        body = None
        if request.method != "GET":
            body, err = await self._read_json_body(request)
            if err:
                return err
        try:
            return upstream.web.json_response(
                await self._router().oauth(
                    request.match_info["provider"],
                    request.match_info["action"],
                    method=request.method,
                    query_string=request.query_string,
                    body=body,
                )
            )
        except NineRouterAPIError as exc:
            return self._router_error(exc)

    async def _handle_list_router_models(self, request):
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        try:
            return upstream.web.json_response(await self._router().list_models())
        except NineRouterAPIError as exc:
            return self._router_error(exc)

    async def _handle_router_usage(self, request):
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        try:
            return upstream.web.json_response(
                await self._router().usage(request.query.get("model", ""))
            )
        except NineRouterAPIError as exc:
            return self._router_error(exc)

    async def _handle_custom_ping(self, request):
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        return upstream.web.json_response(
            {"status": "ok", "extension": "named-agents"}
        )

    async def _handle_get_global_config(self, request):
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        try:
            return upstream.web.json_response(self._config().get_config())
        except ConfigAPIError as exc:
            return self._config_error(exc)

    async def _handle_patch_global_config(self, request):
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        body, err = await self._read_json_body(request)
        if err:
            return err
        try:
            return upstream.web.json_response(self._config().update_config(body))
        except ConfigAPIError as exc:
            return self._config_error(exc)

    async def _handle_list_global_skills(self, request):
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        try:
            return upstream.web.json_response(self._config().list_skills())
        except ConfigAPIError as exc:
            return self._config_error(exc)

    async def _handle_list_agents(self, request):
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        try:
            return upstream.web.json_response(self._agents().list_agents())
        except AgentAPIError as exc:
            return self._agent_error(exc)

    async def _handle_create_agent(self, request):
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        body, err = await self._read_json_body(request)
        if err:
            return err
        try:
            payload, status = self._agents().create_agent(body)
            return upstream.web.json_response(payload, status=status)
        except AgentAPIError as exc:
            return self._agent_error(exc)

    async def _handle_get_agent(self, request):
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        try:
            return upstream.web.json_response(
                self._agents().describe_agent(request.match_info["agent_name"])
            )
        except AgentAPIError as exc:
            return self._agent_error(exc)

    async def _handle_delete_agent(self, request):
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        try:
            return upstream.web.json_response(
                self._agents().delete_agent(request.match_info["agent_name"])
            )
        except AgentAPIError as exc:
            return self._agent_error(exc)

    async def _handle_patch_agent_config(self, request):
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        body, err = await self._read_json_body(request)
        if err:
            return err
        try:
            return upstream.web.json_response(
                self._agents().update_config(request.match_info["agent_name"], body)
            )
        except AgentAPIError as exc:
            return self._agent_error(exc)

    async def _handle_list_agent_skills(self, request):
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        try:
            return upstream.web.json_response(
                self._agents().list_skills(request.match_info["agent_name"])
            )
        except AgentAPIError as exc:
            return self._agent_error(exc)

    async def _handle_install_agent_skill(self, request):
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        body, err = await self._read_json_body(request)
        if err:
            return err
        try:
            return upstream.web.json_response(
                await self._agents().install_skill(request.match_info["agent_name"], body)
            )
        except AgentAPIError as exc:
            return self._agent_error(exc)

    async def _handle_set_agent_skill_enabled(self, request):
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        body, err = await self._read_json_body(request)
        if err:
            return err
        try:
            return upstream.web.json_response(
                self._agents().set_skill_enabled(
                    request.match_info["agent_name"],
                    request.match_info["skill_id"],
                    body,
                )
            )
        except AgentAPIError as exc:
            return self._agent_error(exc)

    async def _handle_remove_agent_skill(self, request):
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        try:
            return upstream.web.json_response(
                self._agents().remove_skill(
                    request.match_info["agent_name"],
                    request.match_info["skill_id"],
                )
            )
        except AgentAPIError as exc:
            return self._agent_error(exc)

    async def _handle_agent_chat(self, request):
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        body, err = await self._read_json_body(request)
        if err:
            return err
        try:
            payload = await self._agents().chat(request.match_info["agent_name"], body)
            status = 200 if payload.get("exit_code") == 0 else 422
            return upstream.web.json_response(payload, status=status)
        except AgentAPIError as exc:
            return self._agent_error(exc)
        except NineRouterAPIError as exc:
            return self._router_error(exc)

    async def _handle_agent_chat_stream(self, request):
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        body, err = await self._read_json_body(request)
        if err:
            return err
        try:
            events = self._agents().chat_stream(request.match_info["agent_name"], body)
            response = upstream.web.StreamResponse(
                status=200,
                headers={
                    "Content-Type": "text/event-stream",
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                },
            )
            await response.prepare(request)
            async for event in events:
                await response.write(event)
            await response.write_eof()
            return response
        except AgentAPIError as exc:
            return self._agent_error(exc)

    async def _handle_list_agent_conversations(self, request):
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        body = {}
        if request.method != "GET":
            body, err = await self._read_json_body(request)
            if err:
                return err
        elif request.query.get("limit"):
            body["limit"] = request.query.get("limit")
        try:
            return upstream.web.json_response(
                self._agents().list_conversations(
                    request.match_info["agent_name"],
                    body,
                )
            )
        except AgentAPIError as exc:
            return self._agent_error(exc)

    async def _handle_create_agent_conversation(self, request):
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        body, err = await self._read_json_body(request)
        if err:
            return err
        try:
            return upstream.web.json_response(
                self._agents().create_conversation(
                    request.match_info["agent_name"],
                    body,
                ),
                status=201,
            )
        except AgentAPIError as exc:
            return self._agent_error(exc)

    async def _handle_get_agent_conversation(self, request):
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        try:
            return upstream.web.json_response(
                self._agents().get_conversation(
                    request.match_info["agent_name"],
                    request.match_info["conversation_id"],
                )
            )
        except AgentAPIError as exc:
            return self._agent_error(exc)

    async def _handle_update_agent_conversation(self, request):
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        body, err = await self._read_json_body(request)
        if err:
            return err
        try:
            return upstream.web.json_response(
                self._agents().update_conversation(
                    request.match_info["agent_name"],
                    request.match_info["conversation_id"],
                    body,
                )
            )
        except AgentAPIError as exc:
            return self._agent_error(exc)

    async def _handle_delete_agent_conversation(self, request):
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        try:
            return upstream.web.json_response(
                self._agents().delete_conversation(
                    request.match_info["agent_name"],
                    request.match_info["conversation_id"],
                )
            )
        except AgentAPIError as exc:
            return self._agent_error(exc)

    async def _handle_list_agent_workspace(self, request):
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        body = {"path": request.query.get("path", ".")}
        try:
            return upstream.web.json_response(
                self._agents().list_workspace(request.match_info["agent_name"], body)
            )
        except AgentAPIError as exc:
            return self._agent_error(exc)

    async def _handle_read_agent_workspace_file(self, request):
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        body, err = await self._read_json_body(request)
        if err:
            return err
        try:
            return upstream.web.json_response(
                self._agents().read_workspace_file(request.match_info["agent_name"], body)
            )
        except AgentAPIError as exc:
            return self._agent_error(exc)

    async def _handle_write_agent_workspace_file(self, request):
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        body, err = await self._read_json_body(request)
        if err:
            return err
        try:
            return upstream.web.json_response(
                self._agents().write_workspace_file(request.match_info["agent_name"], body)
            )
        except AgentAPIError as exc:
            return self._agent_error(exc)

    async def _handle_delete_agent_workspace_path(self, request):
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        body, err = await self._read_json_body(request)
        if err:
            return err
        try:
            return upstream.web.json_response(
                self._agents().delete_workspace_path(request.match_info["agent_name"], body)
            )
        except AgentAPIError as exc:
            return self._agent_error(exc)

    async def _handle_get_agent_memory(self, request):
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        try:
            return upstream.web.json_response(
                self._agents().read_memory(request.match_info["agent_name"])
            )
        except AgentAPIError as exc:
            return self._agent_error(exc)

    async def _handle_patch_agent_memory(self, request):
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        body, err = await self._read_json_body(request)
        if err:
            return err
        try:
            return upstream.web.json_response(
                self._agents().write_memory(request.match_info["agent_name"], body)
            )
        except AgentAPIError as exc:
            return self._agent_error(exc)

    def _register_custom_routes(self, app) -> None:
        if self._custom_routes_registered:
            return
        app.router.add_get("/api/custom/ping", self._handle_custom_ping)
        app.router.add_get("/api/custom/config", self._handle_get_global_config)
        app.router.add_patch("/api/custom/config", self._handle_patch_global_config)
        app.router.add_get("/api/custom/skills", self._handle_list_global_skills)
        app.router.add_get("/api/custom/router", self._handle_router_status)
        app.router.add_get(
            "/api/custom/router/providers",
            self._handle_list_router_providers,
        )
        app.router.add_post(
            "/api/custom/router/providers",
            self._handle_create_router_provider,
        )
        app.router.add_delete(
            "/api/custom/router/providers/{connection_id}",
            self._handle_delete_router_provider,
        )
        app.router.add_post(
            "/api/custom/router/providers/{connection_id}/test",
            self._handle_test_router_provider,
        )
        app.router.add_get(
            "/api/custom/router/oauth/{provider}/{action}",
            self._handle_router_oauth,
        )
        app.router.add_post(
            "/api/custom/router/oauth/{provider}/{action}",
            self._handle_router_oauth,
        )
        app.router.add_get(
            "/api/custom/router/models",
            self._handle_list_router_models,
        )
        app.router.add_get(
            "/api/custom/router/usage",
            self._handle_router_usage,
        )
        app.router.add_get("/api/custom/agents", self._handle_list_agents)
        app.router.add_post("/api/custom/agents", self._handle_create_agent)
        app.router.add_get("/api/custom/agents/{agent_name}", self._handle_get_agent)
        app.router.add_delete(
            "/api/custom/agents/{agent_name}",
            self._handle_delete_agent,
        )
        app.router.add_patch(
            "/api/custom/agents/{agent_name}/config",
            self._handle_patch_agent_config,
        )
        app.router.add_get(
            "/api/custom/agents/{agent_name}/skills",
            self._handle_list_agent_skills,
        )
        app.router.add_post(
            "/api/custom/agents/{agent_name}/skills",
            self._handle_install_agent_skill,
        )
        app.router.add_patch(
            "/api/custom/agents/{agent_name}/skills/{skill_id}",
            self._handle_set_agent_skill_enabled,
        )
        app.router.add_delete(
            "/api/custom/agents/{agent_name}/skills/{skill_id}",
            self._handle_remove_agent_skill,
        )
        app.router.add_post(
            "/api/custom/agents/{agent_name}/chat",
            self._handle_agent_chat,
        )
        app.router.add_post(
            "/api/custom/agents/{agent_name}/chat/stream",
            self._handle_agent_chat_stream,
        )
        app.router.add_get(
            "/api/custom/agents/{agent_name}/conversations",
            self._handle_list_agent_conversations,
        )
        app.router.add_post(
            "/api/custom/agents/{agent_name}/conversations",
            self._handle_create_agent_conversation,
        )
        app.router.add_get(
            "/api/custom/agents/{agent_name}/conversations/{conversation_id}",
            self._handle_get_agent_conversation,
        )
        app.router.add_patch(
            "/api/custom/agents/{agent_name}/conversations/{conversation_id}",
            self._handle_update_agent_conversation,
        )
        app.router.add_delete(
            "/api/custom/agents/{agent_name}/conversations/{conversation_id}",
            self._handle_delete_agent_conversation,
        )
        app.router.add_get(
            "/api/custom/agents/{agent_name}/workspace",
            self._handle_list_agent_workspace,
        )
        app.router.add_post(
            "/api/custom/agents/{agent_name}/workspace/read",
            self._handle_read_agent_workspace_file,
        )
        app.router.add_post(
            "/api/custom/agents/{agent_name}/workspace/write",
            self._handle_write_agent_workspace_file,
        )
        app.router.add_post(
            "/api/custom/agents/{agent_name}/workspace/delete",
            self._handle_delete_agent_workspace_path,
        )
        app.router.add_get(
            "/api/custom/agents/{agent_name}/memory",
            self._handle_get_agent_memory,
        )
        app.router.add_patch(
            "/api/custom/agents/{agent_name}/memory",
            self._handle_patch_agent_memory,
        )
        self._custom_routes_registered = True

    async def connect(self, *args: Any, **kwargs: Any) -> bool:
        original_runner = upstream.web.AppRunner

        def extended_runner(app, *runner_args, **runner_kwargs):
            self._register_custom_routes(app)
            return original_runner(app, *runner_args, **runner_kwargs)

        upstream.web.AppRunner = extended_runner
        try:
            upstream_connect = super().connect
            if "is_reconnect" not in inspect.signature(upstream_connect).parameters:
                kwargs.pop("is_reconnect", None)
            return await upstream_connect(*args, **kwargs)
        finally:
            upstream.web.AppRunner = original_runner


def install() -> None:
    """Install the extended adapter into this gateway process."""
    if upstream.APIServerAdapter is not ExtendedAPIServerAdapter:
        upstream.APIServerAdapter = ExtendedAPIServerAdapter
