"""Conversation preparation and embedded Hermes runner methods."""

import asyncio
import copy
import hashlib
import threading

from xnobrain.runtime_limits import max_parallel_agents, session_timeout_seconds

from .hermes_support import (
    BIG_BROTHER_AGENT_ID,
    LLM_ROUTER_DEFAULT_MODEL,
    MAX_TEXT_CHARS,
    AgentAPIError,
    Any,
    LLMRouterAPIError,
    Mapping,
    Path,
    json,
    re,
    route_llm_model,
    time,
)


class ConversationRunnerMixin:
    _FEATURE_PROMPTS = {
        "todo": (
            "For this turn, begin by creating a concise todo list with the todo tool. "
            "Keep it updated as work progresses and verify every item before finishing."
        ),
        "delegate": (
            "For this turn, identify independent work that benefits from parallelism and "
            "delegate it to sub-agents with delegate_task. Synthesize their results into one answer."
        ),
        "learn": (
            "For this turn, distill the completed work into durable reusable knowledge. "
            "When appropriate, use the skill management tools to create or improve a focused skill."
        ),
        "agent_maker": (
            "Use the agent-maker workflow: interview for missing requirements, then propose a complete reviewable blueprint. "
            "Do not create, scaffold, activate, or approve a profile without explicit human approval."
        ),
        "optimize_skills": (
            "Use skill-optimizer for the selected agent. Review measured usage and clearly label missing or estimated evidence. "
            "Propose a baseline/candidate diff and evaluation plan, but do not evaluate, change, or enable skills without explicit approval."
        ),
    }

    def _mark_agent_active(self, name: str) -> None:
        with self._registry_lock:
            self._active_agent_counts[name] = self._active_agent_counts.get(name, 0) + 1

    def _mark_agent_idle(self, name: str) -> None:
        with self._registry_lock:
            remaining = self._active_agent_counts.get(name, 0) - 1
            if remaining > 0:
                self._active_agent_counts[name] = remaining
            else:
                self._active_agent_counts.pop(name, None)

    def active_agent_ids(self) -> set[str]:
        """Return profiles with at least one model execution in progress."""
        with self._registry_lock:
            return {name for name, count in self._active_agent_counts.items() if count > 0}

    async def chat(self, raw_name: Any, body: Mapping[str, Any]) -> dict[str, Any]:
        prepared = self._prepare_chat_command(raw_name, body, require_conversation=False)
        if prepared["model"] == LLM_ROUTER_DEFAULT_MODEL:
            await self.llm_router.ensure_auto_combo()
        else:
            await self._resolve_prepared_smart_route(prepared)
        name = prepared["name"]
        profile_dir = prepared["profile_dir"]
        before = self._latest_session_ids(profile_dir)

        started = time.time()
        self._mark_agent_active(name)
        try:
            result = await self._run_profile_command(
                name,
                prepared["command"],
                engine=str(prepared.get("engine") or "xnobrain"),
                timeout_seconds=prepared["timeout_seconds"],
            )
        finally:
            self._mark_agent_idle(name)
        provider_error = self._provider_error(result["stdout"])
        if result["exit_code"] != 0 or provider_error:
            raise AgentAPIError(
                provider_error or result["stderr"].strip() or "agent command failed",
                code="provider_request_failed",
                status=502,
            )
        after = self._latest_session_ids(profile_dir)
        session_id = self._detect_changed_session(before, after)
        session = self._session(profile_dir, session_id) if session_id else None
        return {
            "object": "xnobrain.agent_chat",
            "agent": name,
            "conversation_id": session_id,
            "session": session,
            "response": result["stdout"].strip(),
            "stderr": result["stderr"].strip(),
            "exit_code": result["exit_code"],
            "duration_seconds": round(time.time() - started, 3),
            "provider": prepared.get("provider") or "",
            "engine": prepared.get("engine") or "xnobrain",
            "workspace_path": str(self._workspace_dir(name)),
            "profile_path": str(profile_dir),
        }

    def chat_stream(self, raw_name: Any, body: Mapping[str, Any]):
        payload = dict(body)
        if "message" not in payload and "input" in payload:
            payload["message"] = payload.pop("input")
        prepared = self._prepare_chat_command(raw_name, payload, require_conversation=True)
        requested_run_id = str(payload.get("run_id") or "")
        if requested_run_id:
            if not re.fullmatch(r"run_[0-9a-f]{32}", requested_run_id):
                raise AgentAPIError("invalid run id", code="invalid_run")
            prepared["run_id"] = requested_run_id
        prepared["run_mode"] = str(payload.get("run_mode") or "interactive")
        return self._chat_stream_events(prepared)

    def _prepare_chat_command(
        self,
        raw_name: Any,
        body: Mapping[str, Any],
        *,
        require_conversation: bool,
    ) -> dict[str, Any]:
        name = self._agent_name(raw_name)
        profile_dir = self._require_profile(name)
        self._ensure_router_profile(profile_dir, body.get("model"))
        workspace_dir = self._ensure_agent_workspace(name, profile_dir)
        message = self._text_value(body.get("message"), field="message", max_chars=MAX_TEXT_CHARS)
        conversation_id = ""
        if body.get("conversation_id"):
            conversation_id = self._session_id(body["conversation_id"])
        if require_conversation and not conversation_id:
            raise AgentAPIError(
                "conversation is required",
                code="conversation_required",
            )
        if require_conversation and self._session(profile_dir, conversation_id) is None:
            raise AgentAPIError(
                f"Conversation not found: {conversation_id}",
                code="conversation_not_found",
                status=404,
            )
        if conversation_id:
            self._override_stored_runtime_help_guidance(
                profile_dir,
                conversation_id,
                workspace_dir,
            )

        provider = self._conversation_provider(profile_dir, body)
        model = self._conversation_model(profile_dir, body)
        profile_config = self._read_config(profile_dir)
        selection_provider = (
            str(self._get_nested(profile_config, ("model", "selection_provider"), "") or "")
            .strip()
            .lower()
        )
        engine = "xnobrain"
        command = [self._hermes_binary()]
        if conversation_id:
            command.extend(["--resume", conversation_id])
        if body.get("model"):
            command.extend(
                [
                    "--model",
                    route_llm_model(self._nonempty_string(body["model"], "model")),
                ]
            )
        skills = body.get("skills")
        if skills is None:
            disabled = self._disabled_skills(self._read_config(profile_dir))
            skills = (
                [
                    item["skill_id"]
                    for item in self.list_skills(name)["skills"]
                    if item["skill_id"] not in disabled
                ]
                if disabled
                else []
            )
        normalized_skills = self._normalize_skill_list(skills) if skills else []
        if normalized_skills:
            command.extend(["--skills", ",".join(normalized_skills)])
        toolsets = body.get("toolsets")
        if toolsets:
            normalized_toolsets = self._normalize_skill_list(toolsets)
            command.extend(["--toolsets", ",".join(normalized_toolsets)])
        if bool(body.get("yolo", False)):
            command.append("--yolo")
        if require_conversation:
            # Hermes' one-shot mode deliberately bypasses the CLI session
            # loader, even when --resume is present. The quiet query path
            # restores the selected session's SQLite transcript before the
            # turn and persists the new messages back to that same session.
            command.extend(["chat", "--quiet", "--query", message])
        else:
            command.extend(["-z", message])

        timeout_seconds = session_timeout_seconds(body.get("timeout_seconds"))
        feature = str(body.get("feature") or "").strip().lower()
        if feature and feature not in {*self._FEATURE_PROMPTS, "goal"}:
            raise AgentAPIError("invalid composer feature", code="invalid_feature")
        return {
            "name": name,
            "profile_dir": profile_dir,
            "workspace_dir": workspace_dir or self._workspace_dir(name),
            "conversation_id": conversation_id,
            "message": message,
            "provider": provider,
            "selection_provider": selection_provider,
            "model": model,
            "requested_model": (
                self._nonempty_string(body["model"], "model") if body.get("model") else ""
            ),
            "engine": engine,
            "command": command,
            "timeout_seconds": timeout_seconds,
            "feature": feature,
            "goal_resume": bool(body.get("goal_resume", False)),
            "requested_skills": normalized_skills,
            "work_context_id": str((body.get("ownership_context") or {}).get("id") or "personal"),
        }

    async def _resolve_prepared_model_route(self, prepared: dict[str, Any]) -> None:
        """Leave the virtual Auto route for GoRouter to resolve and retry.

        GoRouter v0.0.19 owns allowlist-aware random selection, route health and
        quota filtering, retryable failover, and selected-upstream attribution.
        Resolving Auto in Runtime would duplicate that policy and bypass the
        router's ``AUTO_MAX_TRIES`` bound.
        """
        route_name = str(prepared.get("model") or "").strip()
        if route_name != LLM_ROUTER_DEFAULT_MODEL:
            return
        await self.llm_router.ensure_auto_combo()

    async def _resolve_prepared_smart_route(self, prepared: dict[str, Any]) -> None:
        route_name = str(prepared.get("model") or "").strip()
        # Blend names cannot contain '/', while routed provider model IDs do.
        # Avoid a settings lookup on every ordinary model request.
        if not route_name or route_name == LLM_ROUTER_DEFAULT_MODEL or "/" in route_name:
            return
        decision = await self.llm_router.resolve_blend_route(
            route_name,
            str(prepared.get("message") or ""),
            required_context_tokens=self._estimated_route_context(prepared),
        )
        if decision is None:
            return
        selected_model = str(decision["model"])
        prepared["model"] = selected_model
        prepared["requested_model"] = selected_model
        prepared["smart_route"] = route_name
        prepared["smart_route_tier"] = str(decision.get("tier") or "")
        prepared["route_reasoning"] = str(decision.get("reasoning") or "")
        command = list(prepared.get("command") or [])
        if "--model" in command:
            index = command.index("--model")
            if index + 1 < len(command):
                command[index + 1] = selected_model
        elif command:
            command[1:1] = ["--model", selected_model]
        prepared["command"] = command

    def _estimated_route_context(self, prepared: Mapping[str, Any]) -> int:
        # Reserve output/tool space, then add the last measured prompt size
        # when this is an existing conversation. Unknown measurements remain
        # conservative without preventing models whose metadata is unavailable.
        estimated = 8_192 + max(1, len(str(prepared.get("message") or "")) // 4)
        session_id = str(prepared.get("conversation_id") or "")
        if not session_id:
            return estimated
        session = self._session(Path(prepared["profile_dir"]), session_id) or {}
        raw_config = session.get("model_config")
        try:
            config = json.loads(raw_config) if isinstance(raw_config, str) else raw_config
        except (TypeError, ValueError, json.JSONDecodeError):
            config = {}
        context = config.get("xnobrain_context") if isinstance(config, Mapping) else None
        if isinstance(context, Mapping):
            estimated += max(0, int(context.get("used") or 0))
        return estimated

    @staticmethod
    def _smart_route_step_input(messages: Any) -> str:
        """Build a bounded classifier input for the next model inference."""

        rows = messages if isinstance(messages, list) else []
        selected: list[str] = []
        remaining = 6_000
        for item in reversed(rows[-8:]):
            if not isinstance(item, Mapping) or remaining <= 0:
                continue
            role = str(item.get("role") or "message")
            name = str(item.get("name") or "").strip()
            content = item.get("content")
            if isinstance(content, str):
                text = content
            else:
                try:
                    text = json.dumps(content, ensure_ascii=False, default=str)
                except (TypeError, ValueError):
                    text = str(content or "")
            text = text.strip()
            if not text:
                continue
            label = f"{role} {name}".strip()
            piece = f"{label}: {text}"[-remaining:]
            selected.append(piece)
            remaining -= len(piece)
        selected.reverse()
        return (
            "Classify the difficulty of the agent's next inference step from "
            "this recent execution context:\n" + "\n".join(selected)
        )

    @staticmethod
    def _smart_route_context_tokens(messages: Any) -> int:
        try:
            serialized = json.dumps(messages, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            serialized = str(messages or "")
        return 8_192 + max(1, len(serialized) // 4)

    def _resolve_smart_route_from_worker(
        self,
        loop: asyncio.AbstractEventLoop,
        loop_thread_id: int,
        route_name: str,
        message: str,
        *,
        required_context_tokens: int,
    ) -> dict[str, Any] | None:
        """Resolve through the runtime loop from Hermes' executor thread."""

        if threading.get_ident() == loop_thread_id:
            # Test adapters can execute inline on the event-loop thread. Never
            # block that loop waiting on a coroutine scheduled onto itself.
            return None
        future = asyncio.run_coroutine_threadsafe(
            self.llm_router.resolve_smart_route(
                route_name,
                message,
                required_context_tokens=required_context_tokens,
            ),
            loop,
        )
        try:
            return future.result(timeout=65)
        except Exception:
            future.cancel()
            # Routing is an optimization after the initial turn decision. A
            # transient classifier failure must not fail an active agent run.
            return None

    def _resolve_smart_route_batch_from_worker(
        self,
        loop: asyncio.AbstractEventLoop,
        loop_thread_id: int,
        route_name: str,
        tasks: list[Mapping[str, Any]],
    ) -> list[dict[str, Any] | None]:
        """Classify delegated tasks concurrently, preserving their order."""

        if threading.get_ident() == loop_thread_id:
            return [None] * len(tasks)

        async def resolve_all() -> list[dict[str, Any] | None]:
            async def resolve_one(task: Mapping[str, Any]) -> dict[str, Any] | None:
                goal = str(task.get("goal") or "")
                context = str(task.get("context") or "")
                message = f"Delegated task: {goal}\nContext: {context}".strip()
                try:
                    return await self.llm_router.resolve_smart_route(
                        route_name,
                        message,
                        required_context_tokens=(8_192 + max(1, len(message) // 4)),
                    )
                except Exception:
                    return None

            return list(await asyncio.gather(*(resolve_one(task) for task in tasks)))

        future = asyncio.run_coroutine_threadsafe(resolve_all(), loop)
        try:
            return future.result(timeout=65)
        except Exception:
            future.cancel()
            return [None] * len(tasks)

    @staticmethod
    def _apply_smart_route_decision(
        agent: Any,
        decision: Mapping[str, Any],
        *,
        update_context: bool = True,
    ) -> None:
        model = str(decision.get("model") or "").strip()
        if model:
            agent.model = model
        reasoning = str(decision.get("reasoning") or "").strip().lower()
        if reasoning:
            from hermes_constants import parse_reasoning_effort

            parsed = parse_reasoning_effort(reasoning)
            if parsed is not None:
                agent.reasoning_config = parsed
        context_length = decision.get("context_length")
        compressor = getattr(agent, "context_compressor", None)
        update_model = getattr(compressor, "update_model", None)
        if (
            update_context
            and model
            and callable(update_model)
            and isinstance(context_length, (int, float))
            and context_length > 0
        ):
            update_model(
                model=model,
                context_length=int(context_length),
                base_url=str(getattr(agent, "base_url", "") or ""),
                api_key=getattr(agent, "api_key", ""),
                provider=str(getattr(agent, "provider", "") or ""),
                api_mode=str(getattr(agent, "api_mode", "") or ""),
            )

    @staticmethod
    def _install_provider_runtime_request_guard(agent: Any) -> None:
        """Strip client-only kwargs and custom-provider hints before routing.

        Hermes identifies the centralized endpoint as a named custom provider.
        The OpenAI SDK accepts client-side fields such as ``timeout`` while its
        custom-provider path can also attach ``custom_llm_provider``. GoRouter
        resolves the public model prefix itself and forwards request JSON, so
        neither field belongs in the upstream body.
        """
        if getattr(agent, "_xnobrain_router_tool_codec", False):
            return
        original_build_api_kwargs = getattr(agent, "_build_api_kwargs", None)
        if not callable(original_build_api_kwargs):
            return

        wire_to_runtime: dict[str, str] = {}

        def wire_name(runtime_name: Any) -> str:
            normalized = str(runtime_name or "").strip()
            if not normalized:
                return normalized
            alias = "xno_" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]
            wire_to_runtime[alias] = normalized
            return alias

        def encode_tool(tool: Any) -> Any:
            if not isinstance(tool, dict):
                return tool
            encoded = copy.deepcopy(tool)
            function = encoded.get("function")
            target = function if isinstance(function, dict) else encoded
            runtime_name = str(target.get("name") or "").strip()
            if not runtime_name:
                return encoded
            target["name"] = wire_name(runtime_name)
            description = str(target.get("description") or "").strip()
            identity = f'Runtime tool "{runtime_name}".'
            target["description"] = f"{identity} {description}".strip()
            return encoded

        def encode_message(message: Any) -> Any:
            if not isinstance(message, dict):
                return message
            encoded = copy.deepcopy(message)
            if encoded.get("role") == "tool" and encoded.get("name"):
                encoded["name"] = wire_name(encoded["name"])
            tool_calls = encoded.get("tool_calls")
            if isinstance(tool_calls, list):
                for tool_call in tool_calls:
                    if not isinstance(tool_call, dict):
                        continue
                    function = tool_call.get("function")
                    if isinstance(function, dict) and function.get("name"):
                        function["name"] = wire_name(function["name"])
            return encoded

        def build_provider_runtime_api_kwargs(
            api_messages: list[Any],
            tools_for_api: list[Any] | None = None,
        ) -> dict[str, Any]:
            try:
                kwargs = original_build_api_kwargs(api_messages, tools_for_api=tools_for_api)
            except TypeError as error:
                if "tools_for_api" not in str(error):
                    raise
                kwargs = original_build_api_kwargs(api_messages)
            messages = kwargs.get("messages")
            if isinstance(messages, list):
                kwargs["messages"] = [
                    encode_message(message)
                    for message in messages
                    if not (
                        isinstance(message, dict)
                        and message.get("role") == "assistant"
                        and not message.get("tool_calls")
                        and (message.get("content") is None or message.get("content") == "")
                    )
                ]
            tools = kwargs.get("tools")
            if isinstance(tools, list):
                kwargs["tools"] = [encode_tool(tool) for tool in tools]
            responses_input = kwargs.get("input")
            if isinstance(responses_input, list):
                responses_input = copy.deepcopy(responses_input)
                for item in responses_input:
                    if (
                        isinstance(item, dict)
                        and item.get("type") == "function_call"
                        and item.get("name")
                    ):
                        item["name"] = wire_name(item["name"])
                kwargs["input"] = responses_input
            tool_choice = kwargs.get("tool_choice")
            if isinstance(tool_choice, dict):
                tool_choice = copy.deepcopy(tool_choice)
                function = tool_choice.get("function")
                if isinstance(function, dict) and function.get("name"):
                    function["name"] = wire_name(function["name"])
                elif tool_choice.get("type") == "function" and tool_choice.get("name"):
                    tool_choice["name"] = wire_name(tool_choice["name"])
                kwargs["tool_choice"] = tool_choice
            kwargs.pop("custom_llm_provider", None)
            kwargs.pop("timeout", None)
            extra_body = kwargs.get("extra_body")
            if isinstance(extra_body, dict):
                extra_body.pop("custom_llm_provider", None)
                if not extra_body:
                    kwargs.pop("extra_body", None)
            return kwargs

        agent._build_api_kwargs = build_provider_runtime_api_kwargs

        original_repair_tool_call = getattr(agent, "_repair_tool_call", None)
        if callable(original_repair_tool_call):
            def repair_provider_runtime_tool_call(tool_name: str) -> str | None:
                runtime_name = wire_to_runtime.get(str(tool_name or ""))
                if runtime_name:
                    return runtime_name
                return original_repair_tool_call(tool_name)

            agent._repair_tool_call = repair_provider_runtime_tool_call

        original_tool_gen_started = getattr(agent, "_fire_tool_gen_started", None)
        if callable(original_tool_gen_started):
            def fire_provider_runtime_tool_gen_started(tool_name: str) -> None:
                original_tool_gen_started(wire_to_runtime.get(tool_name, tool_name))

            agent._fire_tool_gen_started = fire_provider_runtime_tool_gen_started

        agent._xnobrain_router_tool_codec = True

    def _install_provider_runtime_child_guards(self, agent: Any) -> None:
        """Apply the router wire codec to every delegated child agent."""
        if getattr(agent, "_xnobrain_router_child_guards", False):
            return
        current = getattr(agent, "_active_children", None)
        if not isinstance(current, list):
            return
        manager = self

        class ProviderRuntimeChildren(list):
            def append(self, child: Any) -> None:
                manager._install_provider_runtime_request_guard(child)
                manager._install_provider_runtime_child_guards(child)
                super().append(child)

        agent._active_children = ProviderRuntimeChildren(current)
        agent._xnobrain_router_child_guards = True

    @staticmethod
    def _install_model_fallbacks(agent: Any, prepared: Mapping[str, Any]) -> None:
        """Attach alternate Auto candidates to Hermes' native failure chain."""
        models = list(
            dict.fromkeys(
                str(model) for model in (prepared.get("model_fallbacks") or []) if str(model)
            )
        )
        if not models:
            return
        common = {
            "provider": "xnobrain",
            "base_url": str(getattr(agent, "base_url", "") or ""),
            "api_key": str(getattr(agent, "api_key", "") or ""),
            "api_mode": str(getattr(agent, "api_mode", "chat_completions") or "chat_completions"),
        }
        chain = [{**common, "model": model} for model in models]
        agent._fallback_chain = chain
        agent._fallback_index = 0
        agent._fallback_model = chain[0]

    def _install_smart_route_step_routing(
        self,
        agent: Any,
        loop: asyncio.AbstractEventLoop,
        loop_thread_id: int,
        route_name: str,
        *,
        apply_reasoning: bool = True,
        reuse_initial_decision: bool = True,
    ) -> None:
        """Re-evaluate a Smart Route after each new tool/agent context step."""

        route_marker = (route_name, apply_reasoning, reuse_initial_decision)
        if getattr(agent, "_xnobrain_smart_step_route", None) == route_marker:
            return
        original_build_api_kwargs = getattr(agent, "_build_api_kwargs", None)
        if not callable(original_build_api_kwargs):
            return
        reuse_initial = reuse_initial_decision
        last_step_input = ""

        def build_smart_route_api_kwargs(api_messages: list[Any]) -> dict[str, Any]:
            nonlocal reuse_initial, last_step_input
            step_input = self._smart_route_step_input(api_messages)
            if reuse_initial:
                # _resolve_prepared_smart_route already classified the first
                # inference. Avoid paying for it twice.
                reuse_initial = False
                last_step_input = step_input
            elif step_input != last_step_input:
                last_step_input = step_input
                decision = self._resolve_smart_route_from_worker(
                    loop,
                    loop_thread_id,
                    route_name,
                    step_input,
                    required_context_tokens=self._smart_route_context_tokens(api_messages),
                )
                if decision is not None:
                    if not apply_reasoning:
                        decision = {**decision, "reasoning": ""}
                    self._apply_smart_route_decision(agent, decision)
            return original_build_api_kwargs(api_messages)

        agent._build_api_kwargs = build_smart_route_api_kwargs
        agent._xnobrain_smart_step_route = route_marker

    def _install_smart_route_child_routing(
        self,
        agent: Any,
        loop: asyncio.AbstractEventLoop,
        loop_thread_id: int,
        route_name: str,
        *,
        delegation_model_pinned: bool = False,
        delegation_reasoning_pinned: bool = False,
    ) -> None:
        """Route every registered child and nested child at inference steps."""

        if getattr(agent, "_xnobrain_smart_children", False):
            return
        current = getattr(agent, "_active_children", None)
        if not isinstance(current, list):
            return
        manager = self

        class SmartRouteChildren(list):
            def append(self, child: Any) -> None:
                if not delegation_model_pinned:
                    manager._install_smart_route_step_routing(
                        child,
                        loop,
                        loop_thread_id,
                        route_name,
                        apply_reasoning=not delegation_reasoning_pinned,
                        reuse_initial_decision=(
                            int(getattr(agent, "_delegate_depth", 0) or 0) == 0
                        ),
                    )
                    manager._install_smart_route_child_routing(
                        child,
                        loop,
                        loop_thread_id,
                        route_name,
                        delegation_model_pinned=delegation_model_pinned,
                        delegation_reasoning_pinned=delegation_reasoning_pinned,
                    )
                super().append(child)

        agent._active_children = SmartRouteChildren(current)
        agent._xnobrain_smart_children = True

    async def _run_session_agent(
        self,
        prepared: Mapping[str, Any],
        *,
        run_id: str,
        stream_delta_callback,
        tool_progress_callback,
        approval_notify_callback,
        agent_ref: list[Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Run one turn through Hermes' native session and approval machinery."""
        from gateway.config import PlatformConfig
        from gateway.platforms.api_server import (
            APIServerAdapter,
            _api_request_profile,
        )
        from gateway.run import _profile_runtime_scope
        from tools.approval import register_gateway_notify, unregister_gateway_notify

        profile_dir = Path(prepared["profile_dir"])
        name = str(prepared["name"])
        workspace_dir = (
            Path(prepared["workspace_dir"]).resolve() if name != BIG_BROTHER_AGENT_ID else None
        )
        conversation_id = str(prepared["conversation_id"])
        manager = self
        runtime_loop = asyncio.get_running_loop()
        runtime_loop_thread_id = threading.get_ident()
        smart_route_name = str(prepared.get("smart_route") or "").strip()
        active_smart_decision: dict[str, Any] = {
            "model": str(prepared.get("model") or ""),
            "reasoning": str(prepared.get("route_reasoning") or ""),
            "tier": str(prepared.get("smart_route_tier") or ""),
        }
        configured_reasoning = (
            str(
                self._get_nested(
                    self._read_config(profile_dir),
                    ("agent", "reasoning_effort"),
                    "medium",
                )
                or "medium"
            )
            .strip()
            .lower()
        )
        if configured_reasoning == "auto" and not smart_route_name:
            try:
                metadata = await self.llm_router.reasoning_for_model(active_smart_decision["model"])
            except LLMRouterAPIError:
                metadata = {}
            active_smart_decision["reasoning"] = str(metadata.get("default_reasoning") or "")

        class RunScopedAPIServerAdapter(APIServerAdapter):
            @staticmethod
            def _bind_api_server_session(
                *,
                chat_id: str = "",
                session_key: str = "",
                session_id: str = "",
            ) -> list:
                # Keep the conversation ID as the stable memory scope passed to
                # _create_agent(), while matching /v1/runs' isolated approval
                # namespace in the executor thread.
                return APIServerAdapter._bind_api_server_session(
                    chat_id=chat_id,
                    session_key=run_id,
                    session_id=session_id,
                )

            def _create_agent(self, *args: Any, **kwargs: Any) -> Any:
                agent = super()._create_agent(*args, **kwargs)
                manager._install_provider_runtime_request_guard(agent)
                manager._install_provider_runtime_child_guards(agent)
                manager._install_model_fallbacks(agent, prepared)
                if smart_route_name:
                    manager._install_smart_route_step_routing(
                        agent,
                        runtime_loop,
                        runtime_loop_thread_id,
                        smart_route_name,
                    )
                # Hermes' API-server surface normally dispatches top-level
                # delegations in the background and relies on GatewayRunner to
                # feed the completed result back into the parent conversation.
                # XNOBrain owns the shorter-lived adapter directly and has no
                # GatewayRunner completion consumer, so detached results would
                # remain pending after this session DB closes. Keep the batch
                # fan-out parallel, but join it inside this originating turn so
                # the parent can synthesize the workers' results reliably.
                from tools.delegate_tool import (
                    _get_max_concurrent_children,
                    _load_config,
                    _strip_model_hidden_task_fields,
                    delegate_task,
                )

                delegation_config = _load_config()
                delegation_model_pinned = any(
                    str(delegation_config.get(key) or "").strip()
                    for key in (
                        "model",
                        "provider",
                        "base_url",
                        "api_mode",
                        "acp_command",
                    )
                )
                delegation_reasoning_pinned = (
                    "reasoning_effort" in delegation_config
                    and delegation_config.get("reasoning_effort") is not None
                )

                if smart_route_name:
                    manager._install_smart_route_child_routing(
                        agent,
                        runtime_loop,
                        runtime_loop_thread_id,
                        smart_route_name,
                        delegation_model_pinned=delegation_model_pinned,
                        delegation_reasoning_pinned=delegation_reasoning_pinned,
                    )

                def dispatch_delegate_sync(function_args: Mapping[str, Any]) -> str:
                    tasks = _strip_model_hidden_task_fields(function_args.get("tasks"))
                    slots = max_parallel_agents(_get_max_concurrent_children())
                    max_batch_tasks = 20
                    if not isinstance(tasks, list):
                        if not smart_route_name:
                            return delegate_task(
                                goal=function_args.get("goal"),
                                context=function_args.get("context"),
                                tasks=tasks,
                                max_iterations=function_args.get("max_iterations"),
                                role=function_args.get("role"),
                                output_schema=function_args.get("output_schema"),
                                background=False,
                                parent_agent=agent,
                            )
                        goal = str(function_args.get("goal") or "").strip()
                        if not goal:
                            return delegate_task(
                                goal=function_args.get("goal"),
                                context=function_args.get("context"),
                                tasks=tasks,
                                max_iterations=function_args.get("max_iterations"),
                                role=function_args.get("role"),
                                output_schema=function_args.get("output_schema"),
                                background=False,
                                parent_agent=agent,
                            )
                        tasks = [
                            {
                                "goal": goal,
                                "context": function_args.get("context"),
                                "role": function_args.get("role"),
                            }
                        ]
                    if not smart_route_name and len(tasks) <= slots:
                        return delegate_task(
                            tasks=tasks,
                            max_iterations=function_args.get("max_iterations"),
                            role=function_args.get("role"),
                            output_schema=function_args.get("output_schema"),
                            background=False,
                            parent_agent=agent,
                        )
                    if not tasks:
                        return delegate_task(
                            tasks=tasks,
                            max_iterations=function_args.get("max_iterations"),
                            role=function_args.get("role"),
                            output_schema=function_args.get("output_schema"),
                            background=False,
                            parent_agent=agent,
                        )
                    if len(tasks) > max_batch_tasks:
                        return json.dumps(
                            {
                                "error": (
                                    f"Too many tasks: {len(tasks)} provided; "
                                    f"this runtime accepts at most {max_batch_tasks}."
                                ),
                            }
                        )

                    decisions = (
                        manager._resolve_smart_route_batch_from_worker(
                            runtime_loop,
                            runtime_loop_thread_id,
                            smart_route_name,
                            tasks,
                        )
                        if smart_route_name and not delegation_model_pinned
                        else [None] * len(tasks)
                    )
                    grouped: dict[tuple[str, str], list[tuple[int, Any]]] = {}
                    decision_by_group: dict[tuple[str, str], Mapping[str, Any] | None] = {}
                    for task_index, task in enumerate(tasks):
                        decision = decisions[task_index] if task_index < len(decisions) else None
                        key = (
                            str((decision or {}).get("model") or agent.model),
                            str((decision or {}).get("reasoning") or ""),
                        )
                        grouped.setdefault(key, []).append((task_index, task))
                        decision_by_group[key] = decision
                    batches: list[tuple[Mapping[str, Any] | None, list[tuple[int, Any]]]] = []
                    for key, entries in grouped.items():
                        for offset in range(0, len(entries), slots):
                            batches.append(
                                (decision_by_group[key], entries[offset : offset + slots])
                            )

                    original_model = agent.model
                    original_reasoning = getattr(agent, "reasoning_config", None)

                    def run_batch(
                        decision: Mapping[str, Any] | None,
                        entries: list[tuple[int, Any]],
                    ) -> str:
                        if decision is not None:
                            manager._apply_smart_route_decision(
                                agent,
                                decision,
                                update_context=False,
                            )
                        try:
                            return delegate_task(
                                tasks=[task for _, task in entries],
                                max_iterations=function_args.get("max_iterations"),
                                role=function_args.get("role"),
                                output_schema=function_args.get("output_schema"),
                                background=False,
                                parent_agent=agent,
                            )
                        finally:
                            agent.model = original_model
                            agent.reasoning_config = original_reasoning

                    if len(batches) == 1 and [index for index, _ in batches[0][1]] == list(
                        range(len(tasks))
                    ):
                        return run_batch(*batches[0])

                    # Hermes treats max_concurrent_children as both a slot
                    # count and a batch-size limit. XNOBrain queues overflow,
                    # and Smart Route additionally groups workers by their own
                    # model/reasoning decision. A mixed-difficulty batch may
                    # therefore execute in a few cost-homogeneous waves.
                    original_callback = getattr(agent, "tool_progress_callback", None)
                    started = time.monotonic()
                    combined_results: list[dict[str, Any]] = []
                    live_transcripts: list[str] = []
                    for task_index, task in enumerate(tasks):
                        if callable(original_callback):
                            original_callback(
                                "subagent.queued",
                                None,
                                str(task.get("goal") or "") if isinstance(task, Mapping) else "",
                                None,
                                task_index=task_index,
                                task_count=len(tasks),
                                concurrency=slots,
                                queue_position=max(0, task_index - slots + 1),
                            )

                    for decision, entries in batches:
                        index_map = [index for index, _ in entries]

                        def relay_chunk_progress(
                            event_type: str,
                            tool_name: str | None = None,
                            preview: str | None = None,
                            event_args: Any = None,
                            **event_kwargs: Any,
                        ) -> None:
                            if not callable(original_callback):
                                return
                            local_index = event_kwargs.get("task_index")
                            if isinstance(local_index, int) and 0 <= local_index < len(index_map):
                                event_kwargs["task_index"] = index_map[local_index]
                            event_kwargs["task_count"] = len(tasks)
                            event_kwargs["concurrency"] = slots
                            original_callback(
                                event_type,
                                tool_name,
                                preview,
                                event_args,
                                **event_kwargs,
                            )

                        agent.tool_progress_callback = relay_chunk_progress
                        try:
                            raw_result = run_batch(decision, entries)
                        finally:
                            agent.tool_progress_callback = original_callback

                        try:
                            decoded = json.loads(raw_result)
                        except (TypeError, ValueError, json.JSONDecodeError):
                            decoded = {}
                        chunk_results = (
                            decoded.get("results") if isinstance(decoded, Mapping) else None
                        )
                        if isinstance(chunk_results, list):
                            for local_index, item in enumerate(chunk_results):
                                entry = (
                                    dict(item)
                                    if isinstance(item, Mapping)
                                    else {
                                        "status": "error",
                                        "error": str(item),
                                    }
                                )
                                reported = int(entry.get("task_index", local_index))
                                entry["task_index"] = (
                                    index_map[reported]
                                    if 0 <= reported < len(index_map)
                                    else index_map[local_index]
                                )
                                combined_results.append(entry)
                        else:
                            error = (
                                str(decoded.get("error") or raw_result)
                                if isinstance(decoded, Mapping)
                                else str(raw_result)
                            )
                            for task_index in index_map:
                                combined_results.append(
                                    {
                                        "task_index": task_index,
                                        "status": "error",
                                        "summary": None,
                                        "error": error,
                                        "api_calls": 0,
                                        "duration_seconds": 0,
                                    }
                                )
                        paths = (
                            decoded.get("live_transcripts")
                            if isinstance(decoded, Mapping)
                            else None
                        )
                        if isinstance(paths, list):
                            live_transcripts.extend(str(path) for path in paths if path)

                    payload: dict[str, Any] = {
                        "results": sorted(
                            combined_results, key=lambda item: int(item.get("task_index", 0))
                        ),
                        "total_duration_seconds": round(time.monotonic() - started, 2),
                        "concurrency": slots,
                    }
                    if live_transcripts:
                        payload["live_transcripts"] = live_transcripts
                    return json.dumps(payload, ensure_ascii=False, default=str)

                agent._dispatch_delegate_task = dispatch_delegate_sync
                # Tell the model that task count and concurrent slot count are
                # separate on this host, otherwise Hermes' stock schema causes
                # it to split a five-task request into multiple tool calls.
                for tool in getattr(agent, "tools", []) or []:
                    function = tool.get("function") if isinstance(tool, dict) else None
                    if not isinstance(function, dict) or function.get("name") != "delegate_task":
                        continue
                    parameters = function.get("parameters")
                    properties = (
                        parameters.get("properties") if isinstance(parameters, dict) else None
                    )
                    tasks_schema = properties.get("tasks") if isinstance(properties, dict) else None
                    if isinstance(tasks_schema, dict):
                        tasks_schema["description"] = (
                            "Batch mode: provide up to 20 independent tasks in one call. "
                            f"The runtime runs at most {max_parallel_agents(_get_max_concurrent_children())} workers "
                            "at once and automatically queues the remainder. Do not split a larger "
                            "batch merely to match the concurrency limit."
                        )
                route_reasoning = str(active_smart_decision.get("reasoning") or "")
                if route_reasoning:
                    manager._apply_smart_route_decision(
                        agent,
                        {
                            "model": str(active_smart_decision.get("model") or ""),
                            "reasoning": route_reasoning,
                        },
                    )
                manager._apply_runtime_help_guidance_override(
                    agent,
                    profile_dir,
                    workspace_dir,
                )
                # Hermes exposes structured model thinking through its
                # reasoning callback. The stock API-server adapter does not
                # register one and instead reports assistant content through
                # tool_progress as `reasoning.available`, which duplicates the
                # visible answer. Bridge the real callback into our SSE stream.
                agent.reasoning_callback = lambda text: tool_progress_callback(
                    "reasoning.delta",
                    "_thinking",
                    str(text or ""),
                    None,
                )
                run_conversation = agent.run_conversation

                def run_with_memory_approval(*run_args: Any, **run_kwargs: Any) -> Any:
                    from agent.runtime_cwd import clear_session_cwd, set_session_cwd
                    from tools import terminal_tool
                    from tools.approval import _await_gateway_decision

                    previous_callback = terminal_tool._get_approval_callback()

                    def approve_memory(
                        command: str,
                        description: str,
                        **_approval_kwargs: Any,
                    ) -> str:
                        decision = _await_gateway_decision(
                            run_id,
                            approval_notify_callback,
                            {
                                "command": command,
                                "description": description,
                                "pattern_key": "memory.write_approval",
                                "pattern_keys": ["memory.write_approval"],
                                "subsystem": "memory",
                                "allow_permanent": True,
                                "allow_session": False,
                            },
                            surface="api_server",
                        )
                        choice = str(decision.get("choice") or "deny")
                        return "once" if choice == "always" else choice

                    terminal_tool.set_approval_callback(approve_memory)
                    task_id = str(run_kwargs.get("task_id") or "")
                    if workspace_dir is not None:
                        set_session_cwd(str(workspace_dir))
                        terminal_tool.register_task_env_overrides(
                            task_id,
                            {"cwd": str(workspace_dir)},
                        )
                    try:
                        return run_conversation(*run_args, **run_kwargs)
                    finally:
                        if workspace_dir is not None:
                            terminal_tool.clear_task_env_overrides(task_id)
                            clear_session_cwd()
                        terminal_tool.set_approval_callback(previous_callback)

                agent.run_conversation = run_with_memory_approval
                return agent

        adapter = RunScopedAPIServerAdapter(PlatformConfig(enabled=True))
        session_db = self._session_db(profile_dir)
        adapter._session_db = session_db
        # XNOBrain may also expose legacy agent directories. Pin the native
        # adapter to the already validated profile path instead of resolving
        # the profile name a second time.
        adapter._profile_scope = lambda _profile: _profile_runtime_scope(profile_dir)
        profile_token = _api_request_profile.set(str(prepared["name"]))
        register_gateway_notify(run_id, approval_notify_callback)
        try:
            session = self._session(profile_dir, conversation_id) or {}
            selected_model = str(
                prepared.get("requested_model")
                or session.get("model")
                or prepared.get("model")
                or ""
            ).strip()
            from hermes_cli.goals import GoalManager

            from .conversation_goals import _goal_payload

            def read_goal():
                with _profile_runtime_scope(profile_dir):
                    return GoalManager(conversation_id).state

            def judge_goal(response: str, user_initiated: bool):
                with _profile_runtime_scope(profile_dir):
                    return GoalManager(conversation_id).evaluate_after_turn(
                        response,
                        user_initiated=user_initiated,
                    )

            def emit_goal(state: Any, decision: Mapping[str, Any] | None = None) -> None:
                tool_progress_callback(
                    "goal.updated",
                    "goal",
                    str((decision or {}).get("reason") or ""),
                    None,
                    goal=_goal_payload(state),
                )

            if str(prepared.get("feature") or "") == "goal":
                with _profile_runtime_scope(profile_dir):
                    GoalManager(conversation_id).set(
                        str(prepared["message"]),
                        max_turns=self._goal_max_turns(profile_dir),
                    )
            initial_goal = read_goal()
            if initial_goal is not None:
                emit_goal(initial_goal)

            prompt = str(prepared["message"])
            feature_prompt = self._FEATURE_PROMPTS.get(str(prepared.get("feature") or ""))
            aggregate_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
            result: dict[str, Any] = {}
            first_turn = True
            skill_tool_start, skill_tool_complete = manager._skill_usage_callbacks(
                prepared,
                run_id=run_id,
            )
            while prompt:
                history = await adapter._conversation_history_for_session(conversation_id)
                if smart_route_name and not first_turn:
                    try:
                        decision = await self.llm_router.resolve_smart_route(
                            smart_route_name,
                            prompt,
                            required_context_tokens=self._smart_route_context_tokens(
                                [*history, {"role": "user", "content": prompt}]
                            ),
                        )
                    except LLMRouterAPIError:
                        decision = None
                    if decision is not None:
                        active_smart_decision.update(decision)
                        selected_model = str(decision.get("model") or selected_model)
                result, turn_usage = await adapter._run_agent(
                    user_message=prompt,
                    conversation_history=history,
                    ephemeral_system_prompt=feature_prompt if first_turn else None,
                    session_id=conversation_id,
                    stream_delta_callback=stream_delta_callback,
                    tool_progress_callback=tool_progress_callback,
                    tool_start_callback=skill_tool_start,
                    tool_complete_callback=skill_tool_complete,
                    agent_ref=agent_ref,
                    gateway_session_key=conversation_id,
                    route={"model": selected_model} if selected_model else None,
                )
                for key in aggregate_usage:
                    aggregate_usage[key] += int(turn_usage.get(key) or 0)
                if bool(result.get("interrupted")) or bool(result.get("failed")):
                    break
                current_goal = read_goal()
                if current_goal is None or str(current_goal.status) != "active":
                    if current_goal is not None:
                        emit_goal(current_goal)
                    break
                output = str(result.get("final_response") or "")
                decision = await asyncio.to_thread(judge_goal, output, first_turn)
                current_goal = read_goal()
                emit_goal(current_goal, decision)
                if not bool(decision.get("should_continue")):
                    break
                prompt = str(decision.get("continuation_prompt") or "")
                first_turn = False
                feature_prompt = None
            usage = aggregate_usage
            agent = agent_ref[0]
            compressor = getattr(agent, "context_compressor", None) if agent is not None else None
            context_used = int(getattr(compressor, "last_prompt_tokens", 0) or 0)
            context_limit = int(getattr(compressor, "context_length", 0) or 0)
            context_threshold = int(getattr(compressor, "threshold_tokens", 0) or 0)
            auto_compaction = (
                bool(getattr(agent, "compression_enabled", False)) if agent is not None else False
            )
            actual_model = str(
                (result.get("model") if isinstance(result, Mapping) else "")
                or getattr(agent, "model", "")
                or prepared.get("model")
                or ""
            ).strip()
            # An auto/blend route can choose models with different windows.
            # Do not report Hermes' generic fallback as a model-specific limit.
            if actual_model.lower() in {"", "auto", LLM_ROUTER_DEFAULT_MODEL.lower()}:
                context_limit = 0
                context_threshold = 0
            context = {
                "used": max(0, context_used),
                "limit": max(0, context_limit),
                "threshold": max(0, context_threshold),
                "auto_compaction": auto_compaction,
                "model": actual_model,
                "route": str(prepared.get("smart_route") or ""),
                "route_tier": str(active_smart_decision.get("tier") or ""),
                "reasoning": str(active_smart_decision.get("reasoning") or ""),
            }
            usage.update(
                {
                    "context_used": context["used"],
                    "context_limit": context["limit"],
                }
            )
            self._persist_conversation_context(profile_dir, conversation_id, context)
            return result, usage
        finally:
            try:
                unregister_gateway_notify(run_id)
            finally:
                _api_request_profile.reset(profile_token)
                close = getattr(session_db, "close", None)
                if callable(close):
                    close()
