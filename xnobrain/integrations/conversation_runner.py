"""Conversation preparation and embedded Hermes runner methods."""

from .hermes_support import (
    AgentAPIError,
    Any,
    BIG_BROTHER_AGENT_ID,
    DEFAULT_CHAT_TIMEOUT_SECONDS,
    MAX_CHAT_TIMEOUT_SECONDS,
    MAX_TEXT_CHARS,
    Mapping,
    NINE_ROUTER_DEFAULT_MODEL,
    Path,
    route_nine_router_model,
    time,
)


class ConversationRunnerMixin:
    async def chat(self, raw_name: Any, body: Mapping[str, Any]) -> dict[str, Any]:
        prepared = self._prepare_chat_command(raw_name, body, require_conversation=False)
        if prepared["model"] == NINE_ROUTER_DEFAULT_MODEL:
            await self.nine_router.ensure_auto_combo()
        name = prepared["name"]
        profile_dir = prepared["profile_dir"]
        before = self._latest_session_ids(profile_dir)

        started = time.time()
        result = await self._run_profile_command(
            name,
            prepared["command"],
            engine=str(prepared.get("engine") or "hermes"),
            timeout_seconds=prepared["timeout_seconds"],
        )
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
            "object": "hermes.agent_chat",
            "agent": name,
            "conversation_id": session_id,
            "session": session,
            "response": result["stdout"].strip(),
            "stderr": result["stderr"].strip(),
            "exit_code": result["exit_code"],
            "duration_seconds": round(time.time() - started, 3),
            "provider": prepared.get("provider") or "",
            "engine": prepared.get("engine") or "hermes",
            "workspace_path": str(self._workspace_dir(name)),
            "profile_path": str(profile_dir),
        }


    def chat_stream(self, raw_name: Any, body: Mapping[str, Any]):
        prepared = self._prepare_chat_command(raw_name, body, require_conversation=True)
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
        engine = "hermes"
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

        timeout_seconds = int(body.get("timeout_seconds") or DEFAULT_CHAT_TIMEOUT_SECONDS)
        timeout_seconds = max(1, min(timeout_seconds, MAX_CHAT_TIMEOUT_SECONDS))
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
        }


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
        # Brain4All may also expose legacy agent directories. Pin the native
        # adapter to the already validated profile path instead of resolving
        # the profile name a second time.
        adapter._profile_scope = lambda _profile: _profile_runtime_scope(profile_dir)
        profile_token = _api_request_profile.set(str(prepared["name"]))
        register_gateway_notify(run_id, approval_notify_callback)
        try:
            history = await adapter._conversation_history_for_session(conversation_id)
            session = self._session(profile_dir, conversation_id) or {}
            selected_model = str(
                prepared.get("requested_model")
                or session.get("model")
                or prepared.get("model")
                or ""
            ).strip()
            result, usage = await adapter._run_agent(
                user_message=str(prepared["message"]),
                conversation_history=history,
                session_id=conversation_id,
                stream_delta_callback=stream_delta_callback,
                tool_progress_callback=tool_progress_callback,
                agent_ref=agent_ref,
                gateway_session_key=conversation_id,
                route={"model": selected_model} if selected_model else None,
            )
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
