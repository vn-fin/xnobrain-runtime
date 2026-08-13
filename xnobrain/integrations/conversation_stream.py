"""Conversation SSE lifecycle and approval methods for the Hermes adapter."""

from .hermes_support import (
    AgentAPIError,
    Any,
    Mapping,
    NINE_ROUTER_DEFAULT_MODEL,
    NineRouterAPIError,
    PROVIDER_ERROR_OUTPUT_RE,
    Path,
    _todo_updated_event,
    asyncio,
    json,
    re,
    time,
    uuid,
)


class ConversationStreamMixin:
    async def _chat_stream_events(self, prepared: Mapping[str, Any]):
        timeout_seconds = int(prepared["timeout_seconds"])
        conversation_id = str(prepared.get("conversation_id") or "")
        model = str(prepared.get("model") or "")
        profile_dir = Path(prepared["profile_dir"])
        skill_ids_before_run = self._skill_ids_for_profile(profile_dir)
        created = int(time.time())
        chat_id = "chatcmpl-" + (conversation_id or uuid.uuid4().hex)
        run_id = "run_" + uuid.uuid4().hex

        if model == NINE_ROUTER_DEFAULT_MODEL:
            try:
                await self.nine_router.ensure_auto_combo()
            except NineRouterAPIError as exc:
                yield self._chat_sse_error(str(exc))
                yield self._chat_sse_done(chat_id, created, model, conversation_id)
                return
        else:
            try:
                await self._resolve_prepared_smart_route(prepared)
                model = str(prepared.get("model") or model)
            except NineRouterAPIError as exc:
                yield self._chat_sse_error(str(exc))
                yield self._chat_sse_done(chat_id, created, model, conversation_id)
                return

        title_task: asyncio.Task[str] | None = None
        if self._conversation_has_default_title(profile_dir, conversation_id):
            # Run the tiny title request beside the chat so it adds no serial
            # model wait to the normal completion path.
            title_task = asyncio.create_task(
                self._summarize_conversation_title(prepared.get("message"), model)
            )

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
        state: dict[str, Any] = {
            "agent": str(prepared.get("name") or ""),
            "conversation_id": conversation_id,
            "agent_ref": None,
            "stop_requested": False,
            "approval_session": run_id,
            "event_loop": loop,
            "event_queue": queue,
        }

        class AgentRef(list):
            def __setitem__(self, index, value):
                super().__setitem__(index, value)
                if state["stop_requested"] and value is not None:
                    value.interrupt("run stopped by user")

        agent_ref: list[Any] = AgentRef([None])
        state["agent_ref"] = agent_ref
        output_chunks: list[str] = []

        def on_delta(delta: str | None) -> None:
            if not delta:
                return
            try:
                running_loop = asyncio.get_running_loop()
            except RuntimeError:
                running_loop = None
            if running_loop is loop:
                queue.put_nowait(("delta", delta))
            else:
                loop.call_soon_threadsafe(queue.put_nowait, ("delta", delta))

        def enqueue_event(event: Mapping[str, Any]) -> None:
            payload = dict(event)
            try:
                running_loop = asyncio.get_running_loop()
            except RuntimeError:
                running_loop = None
            if running_loop is loop:
                queue.put_nowait(("event", payload))
            else:
                loop.call_soon_threadsafe(queue.put_nowait, ("event", payload))

        def on_tool_progress(
            event_type: str,
            tool_name: str | None = None,
            preview: str | None = None,
            args: Any = None,
            **kwargs: Any,
        ) -> None:
            del args
            timestamp = time.time()
            if event_type == "tool.started":
                enqueue_event({
                    "event": "tool.started",
                    "run_id": run_id,
                    "timestamp": timestamp,
                    "tool": tool_name or "tool",
                    "preview": preview or "",
                })
            elif event_type == "tool.completed":
                result = kwargs.get("result")
                write_status = resolve_staged_write(
                    tool_name or "tool",
                    result,
                )
                enqueue_event({
                    "event": "tool.completed",
                    "run_id": run_id,
                    "timestamp": timestamp,
                    "tool": tool_name or "tool",
                    "duration": round(float(kwargs.get("duration") or 0), 3),
                    "error": bool(kwargs.get("is_error", False))
                    or write_status in {"rejected", "failed"},
                })
                if tool_name == "todo" and not bool(kwargs.get("is_error", False)):
                    todo_event = _todo_updated_event(result)
                    if todo_event is not None:
                        enqueue_event({
                            **todo_event,
                            "run_id": run_id,
                            "timestamp": timestamp,
                        })
            elif event_type == "tool.failed":
                enqueue_event({
                    "event": "tool.failed",
                    "run_id": run_id,
                    "timestamp": timestamp,
                    "tool": tool_name or "tool",
                    "duration": round(float(kwargs.get("duration") or 0), 3),
                    "error": True,
                })
            elif event_type == "reasoning.delta":
                enqueue_event({
                    "event": "reasoning.delta",
                    "run_id": run_id,
                    "timestamp": timestamp,
                    "delta": preview or "",
                })

        def on_approval(approval_data: Mapping[str, Any]) -> None:
            from gateway.platforms.api_server import _approval_event_choices
            from gateway.run import _redact_approval_command

            smart_denied = bool(approval_data.get("smart_denied"))
            allow_permanent = approval_data.get("allow_permanent") is not False
            event = {
                "event": "approval.request",
                "run_id": run_id,
                "timestamp": time.time(),
                "command": _redact_approval_command(approval_data.get("command")),
                "description": str(approval_data.get("description") or "Approval required"),
                "pattern_keys": list(approval_data.get("pattern_keys") or []),
                "smart_denied": smart_denied,
                "allow_permanent": allow_permanent,
                "choices": _approval_event_choices(
                    smart_denied=smart_denied,
                    allow_permanent=allow_permanent,
                ),
            }
            subsystem = str(approval_data.get("subsystem") or "")
            pending_id = str(approval_data.get("pending_id") or "")
            if subsystem in {"memory", "skills"}:
                event["subsystem"] = subsystem
            if pending_id:
                event["pending_id"] = pending_id
            enqueue_event(event)

        def resolve_staged_write(tool_name: str, raw_result: Any) -> str:
            """Turn Hermes' staged write result into a run-scoped approval."""
            if isinstance(raw_result, Mapping):
                result = dict(raw_result)
            elif isinstance(raw_result, str):
                try:
                    decoded = json.loads(raw_result)
                except (TypeError, ValueError):
                    return ""
                result = dict(decoded) if isinstance(decoded, Mapping) else {}
            else:
                return ""
            pending_id = str(result.get("pending_id") or "")
            if not result.get("staged") or not pending_id:
                return ""

            normalized_tool = tool_name.lower()
            if normalized_tool == "memory" or normalized_tool.endswith("__memory"):
                subsystem = "memory"
            elif "skill_manage" in normalized_tool:
                subsystem = "skills"
            else:
                return ""

            from tools import write_approval
            from tools.approval import _await_gateway_decision

            record = write_approval.get_pending(subsystem, pending_id)
            if record is None:
                enqueue_event({
                    "event": "write.failed",
                    "run_id": run_id,
                    "timestamp": time.time(),
                    "tool": tool_name,
                    "subsystem": subsystem,
                    "pending_id": pending_id,
                    "status": "missing",
                })
                return "failed"

            summary = str(record.get("summary") or f"Pending {subsystem} write")
            decision = _await_gateway_decision(
                run_id,
                on_approval,
                {
                    "command": summary,
                    "description": (
                        "Memory write requires approval"
                        if subsystem == "memory"
                        else "Skill write requires approval"
                    ),
                    "pattern_key": f"{subsystem}.write_approval",
                    "pattern_keys": [f"{subsystem}.write_approval"],
                    "subsystem": subsystem,
                    "pending_id": pending_id,
                    "allow_permanent": True,
                    "allow_session": False,
                },
                surface="api_server",
            )
            choice = str(decision.get("choice") or "")
            if not decision.get("resolved") or not choice:
                status = "pending"
            elif choice == "deny":
                write_approval.discard_pending(subsystem, pending_id)
                status = "rejected"
            else:
                try:
                    if subsystem == "memory":
                        from tools.memory_tool import apply_memory_pending, load_on_disk_store

                        applied = apply_memory_pending(
                            dict(record.get("payload") or {}),
                            load_on_disk_store(),
                        )
                        success = bool(applied.get("success"))
                    else:
                        from tools.skill_manager_tool import apply_skill_pending

                        applied = json.loads(
                            apply_skill_pending(dict(record.get("payload") or {}))
                        )
                        success = bool(applied.get("success"))
                except Exception:
                    success = False
                if success:
                    write_approval.discard_pending(subsystem, pending_id)
                    status = "applied"
                else:
                    status = "failed"

            enqueue_event({
                "event": f"write.{status}",
                "run_id": run_id,
                "timestamp": time.time(),
                "tool": tool_name,
                "subsystem": subsystem,
                "pending_id": pending_id,
                "status": status,
            })
            return status

        async def run_agent() -> None:
            try:
                result, usage = await self._run_session_agent(
                    prepared,
                    run_id=run_id,
                    stream_delta_callback=on_delta,
                    tool_progress_callback=on_tool_progress,
                    approval_notify_callback=on_approval,
                    agent_ref=agent_ref,
                )
                # Skills created or downloaded during chat are opt-in for the
                # next turn, regardless of which Hermes install path created them.
                self._disable_new_skills(profile_dir, skill_ids_before_run)
                # Flush callbacks already scheduled from the worker thread
                # before placing the terminal event behind them.
                await asyncio.sleep(0)
                await queue.put(("completed", (result, usage)))
            except Exception as exc:
                try:
                    self._disable_new_skills(profile_dir, skill_ids_before_run)
                except Exception:
                    pass
                await queue.put(("failed", exc))

        task = asyncio.create_task(run_agent())
        state["task"] = task
        self._active_runs[run_id] = state
        yield self._sse_data({
            "event": "run.started", "run_id": run_id,
            "session_id": conversation_id, "status": "started",
            "timestamp": time.time(), "model": model,
        })
        deadline = time.monotonic() + timeout_seconds
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise asyncio.TimeoutError
                try:
                    kind, payload = await asyncio.wait_for(
                        queue.get(),
                        timeout=min(15.0, remaining),
                    )
                except asyncio.TimeoutError:
                    if time.monotonic() < deadline:
                        yield b": keepalive\n\n"
                        continue
                    raise
                if kind == "delta":
                    text = str(payload)
                    output_chunks.append(text)
                    yield self._sse_data({
                        "event": "message.delta",
                        "run_id": run_id,
                        "timestamp": time.time(),
                        "delta": text,
                    })
                    continue
                if kind == "event":
                    yield self._sse_data(payload)
                    continue
                if kind == "failed":
                    raise payload

                result, usage = payload
                output = str(result.get("final_response") or "")
                if not output_chunks and output:
                    # Some non-streaming-compatible providers can only return
                    # a final response. Preserve a usable fallback for them.
                    output_chunks.append(output)
                    yield self._sse_data({
                        "event": "message.delta",
                        "run_id": run_id,
                        "timestamp": time.time(),
                        "delta": output,
                    })
                if state["stop_requested"] or bool(result.get("interrupted")):
                    yield self._sse_data({
                        "event": "run.cancelled",
                        "run_id": run_id,
                        "timestamp": time.time(),
                    })
                elif result.get("failed") and not output:
                    yield self._sse_data({
                        "event": "run.failed",
                        "run_id": run_id,
                        "timestamp": time.time(),
                        "message": str(result.get("error") or "agent command failed"),
                    })
                else:
                    suggested_title = await title_task if title_task is not None else None
                    conversation_title = self._auto_title_conversation(
                        profile_dir,
                        conversation_id,
                        prepared.get("message"),
                        suggested_title=suggested_title,
                    )
                    yield self._sse_data({
                        "event": "run.completed",
                        "run_id": run_id,
                        "timestamp": time.time(),
                        "output": output,
                        "usage": {
                            "input_tokens": int(usage.get("input_tokens") or 0),
                            "output_tokens": int(usage.get("output_tokens") or 0),
                            "total_tokens": int(usage.get("total_tokens") or 0),
                        },
                        "conversation_title": conversation_title or "",
                    })
                yield b"data: [DONE]\n\n"
                return
        except asyncio.CancelledError:
            self._stopped_runs.add(run_id)
            state["stop_requested"] = True
            agent = agent_ref[0]
            if agent is not None:
                agent.interrupt("client disconnected")
            try:
                from tools.approval import unregister_gateway_notify

                unregister_gateway_notify(run_id)
            except ImportError:
                pass
            if not task.done():
                try:
                    await asyncio.wait_for(asyncio.shield(task), timeout=5)
                except asyncio.TimeoutError:
                    pass
            raise
        except asyncio.TimeoutError:
            state["stop_requested"] = True
            agent = agent_ref[0]
            if agent is not None:
                agent.interrupt("agent command timed out")
            try:
                from tools.approval import unregister_gateway_notify

                unregister_gateway_notify(run_id)
            except ImportError:
                pass
            yield self._sse_data({
                "event": "run.failed",
                "run_id": run_id,
                "timestamp": time.time(),
                "message": "agent command timed out",
            })
            yield b"data: [DONE]\n\n"
        except Exception as exc:
            yield self._sse_data({
                "event": "run.failed",
                "run_id": run_id,
                "timestamp": time.time(),
                "message": str(exc) or "agent command failed",
            })
            yield b"data: [DONE]\n\n"
        finally:
            if title_task is not None and not title_task.done():
                title_task.cancel()
            self._active_runs.pop(run_id, None)
            self._stopped_runs.discard(run_id)


    async def stop_run(self, run_id: str) -> dict[str, Any]:
        if not re.fullmatch(r"run_[0-9a-f]{32}", str(run_id)):
            raise AgentAPIError("invalid run id", code="invalid_run")
        state = self._active_runs.get(run_id)
        if state is None or state["task"].done():
            raise AgentAPIError("run not found", code="run_not_found", status=404)
        self._stopped_runs.add(run_id)
        state["stop_requested"] = True
        agent = state["agent_ref"][0]
        if agent is not None:
            await asyncio.to_thread(agent.interrupt, "run stopped by user")
        try:
            from tools.approval import unregister_gateway_notify

            unregister_gateway_notify(run_id)
        except ImportError:
            pass
        return {"run_id": run_id, "stopped": True, "status": "stopping"}


    def resolve_approval(self, run_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        choice = str(body.get("choice") or "").strip().lower()
        if choice not in {"once", "session", "always", "deny"}:
            raise AgentAPIError("invalid approval choice", code="invalid_approval_choice")
        try:
            from tools.approval import resolve_gateway_approval
            state = self._active_runs.get(run_id)
            approval_session = (
                str(state.get("approval_session") or run_id)
                if state is not None
                else run_id
            )
            resolved = resolve_gateway_approval(
                approval_session,
                choice,
                bool(body.get("resolve_all", False)),
            )
        except ImportError as error:
            raise AgentAPIError("Hermes approval core is unavailable", code="approval_unavailable", status=503) from error
        if resolved == 0:
            raise AgentAPIError("run has no pending approval", code="approval_not_pending", status=409)
        if state is not None:
            event_loop = state.get("event_loop")
            event_queue = state.get("event_queue")
            if event_loop is not None and event_queue is not None:
                event_loop.call_soon_threadsafe(
                    event_queue.put_nowait,
                    (
                        "event",
                        {
                            "event": "approval.responded",
                            "run_id": run_id,
                            "timestamp": time.time(),
                            "choice": choice,
                            "resolved": resolved,
                        },
                    ),
                )
        return {"run_id": run_id, "choice": choice, "resolved": resolved}


    def _chat_sse_chunk(
        self,
        chat_id: str,
        created: int,
        model: str,
        conversation_id: str,
        content: str,
        *,
        include_role: bool,
    ) -> bytes:
        delta: dict[str, Any] = {}
        if include_role:
            delta["role"] = "assistant"
        if content:
            delta["content"] = content
        return self._sse_data(
            {
                "id": chat_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "conversation_id": conversation_id,
                "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
            }
        )


    def _chat_sse_done(self, chat_id: str, created: int, model: str, conversation_id: str) -> bytes:
        return self._sse_data(
            {
                "id": chat_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "conversation_id": conversation_id,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            }
        ) + b"data: [DONE]\n\n"


    def _chat_sse_error(self, message: str) -> bytes:
        payload = json.dumps({"error": {"message": message}}, separators=(",", ":"))
        return f"event: error\ndata: {payload}\n\n".encode("utf-8")


    @staticmethod
    def _provider_error(output: str) -> str:
        """Recognize the structured provider failure Hermes may print with exit 0."""
        normalized = str(output or "").strip()
        return normalized if PROVIDER_ERROR_OUTPUT_RE.match(normalized) else ""


    def _sse_data(self, payload: Mapping[str, Any]) -> bytes:
        raw = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
        return f"data: {raw}\n\n".encode("utf-8")
