"""Conversation preparation and embedded Hermes runner methods."""

import asyncio

from .hermes_support import (
    AgentAPIError,
    Any,
    BIG_BROTHER_AGENT_ID,
    MAX_TEXT_CHARS,
    Mapping,
    NINE_ROUTER_DEFAULT_MODEL,
    Path,
    json,
    route_nine_router_model,
    re,
    time,
)
from xnobrain.runtime_limits import max_parallel_agents, session_timeout_seconds


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
        if prepared["model"] == NINE_ROUTER_DEFAULT_MODEL:
            await self.nine_router.ensure_auto_combo()
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
        engine = "xnobrain"
        command = [self._hermes_binary()]
        if conversation_id:
            command.extend(["--resume", conversation_id])
        if body.get("model"):
            command.extend([
                "--model",
                route_nine_router_model(
                    self._nonempty_string(body["model"], "model")
                ),
            ])
        skills = body.get("skills")
        if skills is None:
            disabled = self._disabled_skills(self._read_config(profile_dir))
            skills = [
                item["skill_id"]
                for item in self.list_skills(name)["skills"]
                if item["skill_id"] not in disabled
            ] if disabled else []
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
            "model": model,
            "requested_model": (
                self._nonempty_string(body["model"], "model")
                if body.get("model")
                else ""
            ),
            "engine": engine,
            "command": command,
            "timeout_seconds": timeout_seconds,
            "feature": feature,
            "goal_resume": bool(body.get("goal_resume", False)),
        }


    async def _resolve_prepared_smart_route(self, prepared: dict[str, Any]) -> None:
        route_name = str(prepared.get("model") or "").strip()
        # Blend names cannot contain '/', while routed provider model IDs do.
        # Avoid a settings lookup on every ordinary model request.
        if not route_name or route_name == NINE_ROUTER_DEFAULT_MODEL or "/" in route_name:
            return
        decision = await self.nine_router.resolve_smart_route(
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
    def _apply_provider_runtime_compatibility(agent: Any, model: str) -> None:
        """Apply narrow workarounds for known router/provider wire defects."""
        owner = str(model or "").strip().split("/", 1)[0]
        if owner in {"oc", "ocg", "ocz"}:
            # OpenCode currently terminates otherwise-valid SSE responses with
            # [DONE] but no OpenAI finish_reason. Hermes correctly treats that
            # shape as a dropped stream and requests continuations, duplicating
            # the answer. The blocking response is complete, so use it until
            # 9router normalizes OpenCode's terminal event.
            agent._disable_streaming = True


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
            Path(prepared["workspace_dir"]).resolve()
            if name != BIG_BROTHER_AGENT_ID
            else None
        )
        conversation_id = str(prepared["conversation_id"])
        manager = self

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
                manager._apply_provider_runtime_compatibility(
                    agent,
                    str(prepared.get("model") or ""),
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
                    _strip_model_hidden_task_fields,
                    delegate_task,
                )

                def dispatch_delegate_sync(function_args: Mapping[str, Any]) -> str:
                    tasks = _strip_model_hidden_task_fields(function_args.get("tasks"))
                    slots = max_parallel_agents(_get_max_concurrent_children())
                    max_batch_tasks = 20
                    if not isinstance(tasks, list) or len(tasks) <= slots:
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
                    if len(tasks) > max_batch_tasks:
                        return json.dumps({
                            "error": (
                                f"Too many tasks: {len(tasks)} provided; "
                                f"this runtime accepts at most {max_batch_tasks}."
                            ),
                        })

                    # Hermes currently treats max_concurrent_children as both
                    # a slot count and a hard batch-size limit. XNOBrain keeps
                    # the configured number of execution slots, queues the
                    # remainder, and presents the model with one consolidated
                    # tool result. Each wave is still internally parallel.
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

                    for offset in range(0, len(tasks), slots):
                        chunk = tasks[offset:offset + slots]

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
                            if isinstance(local_index, int):
                                event_kwargs["task_index"] = offset + local_index
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
                            raw_result = delegate_task(
                                tasks=chunk,
                                max_iterations=function_args.get("max_iterations"),
                                role=function_args.get("role"),
                                background=False,
                                parent_agent=agent,
                            )
                        finally:
                            agent.tool_progress_callback = original_callback

                        try:
                            decoded = json.loads(raw_result)
                        except (TypeError, ValueError, json.JSONDecodeError):
                            decoded = {}
                        chunk_results = decoded.get("results") if isinstance(decoded, Mapping) else None
                        if isinstance(chunk_results, list):
                            for local_index, item in enumerate(chunk_results):
                                entry = dict(item) if isinstance(item, Mapping) else {
                                    "status": "error",
                                    "error": str(item),
                                }
                                entry["task_index"] = offset + int(entry.get("task_index", local_index))
                                combined_results.append(entry)
                        else:
                            error = str(decoded.get("error") or raw_result) if isinstance(decoded, Mapping) else str(raw_result)
                            for local_index in range(len(chunk)):
                                combined_results.append({
                                    "task_index": offset + local_index,
                                    "status": "error",
                                    "summary": None,
                                    "error": error,
                                    "api_calls": 0,
                                    "duration_seconds": 0,
                                })
                        paths = decoded.get("live_transcripts") if isinstance(decoded, Mapping) else None
                        if isinstance(paths, list):
                            live_transcripts.extend(str(path) for path in paths if path)

                    payload: dict[str, Any] = {
                        "results": sorted(combined_results, key=lambda item: int(item.get("task_index", 0))),
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
                    properties = parameters.get("properties") if isinstance(parameters, dict) else None
                    tasks_schema = properties.get("tasks") if isinstance(properties, dict) else None
                    if isinstance(tasks_schema, dict):
                        tasks_schema["description"] = (
                            "Batch mode: provide up to 20 independent tasks in one call. "
                            f"The runtime runs at most {max_parallel_agents(_get_max_concurrent_children())} workers "
                            "at once and automatically queues the remainder. Do not split a larger "
                            "batch merely to match the concurrency limit."
                        )
                route_reasoning = str(prepared.get("route_reasoning") or "")
                if route_reasoning in {"low", "medium", "high"}:
                    agent.reasoning_config = {
                        "enabled": True,
                        "effort": route_reasoning,
                    }
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
            while prompt:
                history = await adapter._conversation_history_for_session(conversation_id)
                result, turn_usage = await adapter._run_agent(
                    user_message=prompt,
                    conversation_history=history,
                    ephemeral_system_prompt=feature_prompt if first_turn else None,
                    session_id=conversation_id,
                    stream_delta_callback=stream_delta_callback,
                    tool_progress_callback=tool_progress_callback,
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
            auto_compaction = bool(getattr(agent, "compression_enabled", False)) if agent is not None else False
            actual_model = str(
                (result.get("model") if isinstance(result, Mapping) else "")
                or getattr(agent, "model", "")
                or prepared.get("model")
                or ""
            ).strip()
            # An auto/blend route can choose models with different windows.
            # Do not report Hermes' generic fallback as a model-specific limit.
            if actual_model.lower() in {"", "auto", NINE_ROUTER_DEFAULT_MODEL.lower()}:
                context_limit = 0
                context_threshold = 0
            context = {
                "used": max(0, context_used),
                "limit": max(0, context_limit),
                "threshold": max(0, context_threshold),
                "auto_compaction": auto_compaction,
                "model": actual_model,
                "route": str(prepared.get("smart_route") or ""),
                "route_tier": str(prepared.get("smart_route_tier") or ""),
                "reasoning": str(prepared.get("route_reasoning") or ""),
            }
            usage.update({
                "context_used": context["used"],
                "context_limit": context["limit"],
            })
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
