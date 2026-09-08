"""Conversation SSE lifecycle and approval methods for the Hermes adapter."""

from xnobrain.runtime_limits import max_parallel_agents

from ..services.public_text import public_error_message
from .hermes_support import (
    LLM_ROUTER_DEFAULT_MODEL,
    PROVIDER_ERROR_OUTPUT_RE,
    AgentAPIError,
    Any,
    LLMRouterAPIError,
    Mapping,
    Path,
    _todo_updated_event,
    asyncio,
    json,
    re,
    time,
    uuid,
)


def _commit_resolved_write_result(agent, tool_name, pending_id, applied) -> bool:
    """Replace a staged tool row before Hermes sends it back to the model."""
    if agent is None or not isinstance(applied, Mapping):
        return False
    committed = dict(applied)
    committed.pop("pending_id", None)
    committed["staged"] = False
    committed["disposition"] = str(committed.get("disposition") or "applied")
    messages = getattr(agent, "_db_flush_scan_prefix", None)
    if not isinstance(messages, list):
        messages = getattr(agent, "_session_messages", None)
    if not isinstance(messages, list):
        return False
    replacement = json.dumps(committed, ensure_ascii=False, default=str)
    for message in reversed(messages):
        if not isinstance(message, dict) or message.get("role") != "tool":
            continue
        if str(message.get("name") or "") != str(tool_name):
            continue
        if pending_id not in str(message.get("content") or ""):
            continue
        message["content"] = replacement
        session_db = getattr(agent, "_session_db", None)
        session_id = str(getattr(agent, "session_id", "") or "")
        if session_db is not None and session_id:
            # The staged row was flushed immediately before the progress callback.
            session_db.replace_messages(session_id, messages, active_only=True)
        return True
    return False


def _persist_cancelled_terminal(agent, output: str = "") -> bool:
    """Persist a visible terminal marker so a stopped run survives reload."""
    if agent is None:
        return False
    session_db = getattr(agent, "_session_db", None)
    session_id = str(getattr(agent, "session_id", "") or "")
    if session_db is None or not session_id:
        return False
    partial = str(output or "").strip()
    marker = "Response stopped by user. Active and queued work was cancelled; completed and partial output is retained above."
    content = f"{partial}\n\n---\n\n{marker}" if partial else marker
    message = {
        "role": "assistant",
        "content": content,
        "finish_reason": "cancelled",
        "timestamp": time.time(),
    }
    session_db.append_messages_batch(session_id, [message])
    session_messages = getattr(agent, "_session_messages", None)
    if isinstance(session_messages, list):
        session_messages.append(message)
    return True


class ConversationStreamMixin:
    async def _chat_stream_events(self, prepared: Mapping[str, Any]):
        timeout_seconds = int(prepared["timeout_seconds"])
        conversation_id = str(prepared.get("conversation_id") or "")
        model = str(prepared.get("model") or "")
        profile_dir = Path(prepared["profile_dir"])
        skill_ids_before_run = self._skill_ids_for_profile(profile_dir)
        created = int(time.time())
        chat_id = "chatcmpl-" + (conversation_id or uuid.uuid4().hex)
        run_id = str(prepared.get("run_id") or ("run_" + uuid.uuid4().hex))

        self._skill_usage_record_requested(prepared, run_id=run_id)

        try:
            await self._resolve_prepared_model_route(prepared)
            await self._resolve_prepared_smart_route(prepared)
            model = str(prepared.get("model") or model)
        except LLMRouterAPIError as exc:
            yield self._chat_sse_error(str(exc))
            yield self._chat_sse_done(chat_id, created, model, conversation_id)
            return

        title_task: asyncio.Task[str] | None = None
        if self._conversation_has_default_title(profile_dir, conversation_id):
            title_model = model
            smart_route_name = str(prepared.get("smart_route") or "").strip()
            if smart_route_name:
                try:
                    title_route = await self.llm_router.resolve_smart_route(
                        smart_route_name,
                        "hello",
                        required_context_tokens=(
                            8_192 + max(1, len(str(prepared.get("message") or "")) // 4)
                        ),
                    )
                except LLMRouterAPIError:
                    title_route = None
                if title_route is not None:
                    title_model = str(title_route.get("model") or title_model)
            # Run the tiny title request beside the chat so it adds no serial
            # model wait to the normal completion path.
            title_task = asyncio.create_task(
                self._summarize_conversation_title(prepared.get("message"), title_model)
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
            timestamp = time.time()
            normalized_tool = tool_name or "tool"

            def safe_text(value: Any, limit: int = 2_000) -> str:
                text = str(value or "")
                try:
                    from agent.redact import redact_sensitive_text

                    text = redact_sensitive_text(text, force=True) or ""
                except Exception:
                    text = "[event text withheld: redaction unavailable]"
                return text[:limit]

            def safe_delegation_output(value: Any) -> str:
                """Keep UI result data while withholding local transcript paths."""
                if isinstance(value, str):
                    try:
                        decoded = json.loads(value)
                    except (TypeError, ValueError):
                        return safe_text(value, 20_000)
                elif isinstance(value, Mapping):
                    decoded = dict(value)
                else:
                    return json.dumps(value, ensure_ascii=False, default=str)
                if not isinstance(decoded, Mapping):
                    return json.dumps(decoded, ensure_ascii=False, default=str)
                sanitized = dict(decoded)
                sanitized.pop("live_transcripts", None)
                results = sanitized.get("results")
                if isinstance(results, list):
                    sanitized["results"] = [
                        {
                            key: item_value
                            for key, item_value in item.items()
                            if key not in {"live_transcript", "transcript_path"}
                        }
                        if isinstance(item, Mapping)
                        else item
                        for item in results
                    ]
                return json.dumps(sanitized, ensure_ascii=False, default=str)

            def live_subagent_usage(subagent_id: Any) -> dict[str, int]:
                """Snapshot counters already recorded by a running Hermes child."""
                if not isinstance(subagent_id, str) or not subagent_id:
                    return {}
                parent = agent_ref[0]
                if parent is None:
                    return {}
                lock = getattr(parent, "_active_children_lock", None)
                try:
                    if lock is not None:
                        with lock:
                            children = list(getattr(parent, "_active_children", ()))
                    else:
                        children = list(getattr(parent, "_active_children", ()))
                    child = next(
                        (
                            item
                            for item in children
                            if getattr(item, "_subagent_id", None) == subagent_id
                        ),
                        None,
                    )
                    if child is None:
                        return {}
                    return {
                        "api_calls": int(getattr(child, "session_api_calls", 0) or 0),
                        "input_tokens": int(getattr(child, "session_input_tokens", 0) or 0),
                        "output_tokens": int(getattr(child, "session_output_tokens", 0) or 0),
                        "reasoning_tokens": int(getattr(child, "session_reasoning_tokens", 0) or 0),
                    }
                except (AttributeError, RuntimeError, TypeError, ValueError):
                    return {}

            if event_type == "goal.updated":
                enqueue_event(
                    {
                        "event": "goal.updated",
                        "run_id": run_id,
                        "timestamp": timestamp,
                        "goal": kwargs.get("goal"),
                    }
                )
                return

            if event_type.startswith("subagent."):
                event_name = {
                    "subagent.queued": "delegation.worker.queued",
                    "subagent.start": "delegation.worker.started",
                    "subagent.tool": "delegation.worker.activity",
                    "subagent.thinking": "delegation.worker.activity",
                    "subagent.text": "delegation.worker.text",
                    "subagent.complete": "delegation.worker.completed",
                }.get(event_type)
                if event_name is None:
                    return
                task_index = kwargs.get("task_index")
                if not isinstance(task_index, int):
                    return
                event = {
                    "event": event_name,
                    "run_id": run_id,
                    "timestamp": timestamp,
                    "task_index": task_index,
                    "task_count": int(kwargs.get("task_count") or 1),
                    "concurrency": int(kwargs.get("concurrency") or 1),
                    "goal": safe_text(kwargs.get("goal") or preview),
                }
                if event_type != "subagent.queued":
                    event.update(live_subagent_usage(kwargs.get("subagent_id")))
                if event_type == "subagent.queued":
                    event["queue_position"] = int(kwargs.get("queue_position") or 0)
                elif event_type == "subagent.tool":
                    event.update(
                        {
                            "kind": "tool",
                            "tool": safe_text(tool_name, 120),
                            "message": safe_text(preview, 500),
                            "tool_count": int(kwargs.get("tool_count") or 0),
                        }
                    )
                elif event_type == "subagent.thinking":
                    event.update({"kind": "activity", "message": "Reasoning"})
                elif event_type == "subagent.text":
                    event["delta"] = safe_text(preview, 4_000)
                elif event_type == "subagent.complete":
                    event.update(
                        {
                            "status": safe_text(kwargs.get("status"), 40) or "completed",
                            "duration_seconds": round(
                                float(kwargs.get("duration_seconds") or 0), 3
                            ),
                            "summary": safe_text(kwargs.get("summary") or preview, 4_000),
                            "input_tokens": int(kwargs.get("input_tokens") or 0),
                            "output_tokens": int(kwargs.get("output_tokens") or 0),
                            "reasoning_tokens": int(kwargs.get("reasoning_tokens") or 0),
                            "api_calls": int(kwargs.get("api_calls") or 0),
                            "files_read": [
                                safe_text(path, 500)
                                for path in list(kwargs.get("files_read") or [])[:40]
                            ],
                            "files_written": [
                                safe_text(path, 500)
                                for path in list(kwargs.get("files_written") or [])[:40]
                            ],
                        }
                    )
                enqueue_event(event)
                return

            if event_type == "tool.started":
                event: dict[str, Any] = {
                    "event": "tool.started",
                    "run_id": run_id,
                    "timestamp": timestamp,
                    "tool": normalized_tool,
                    "preview": preview or "",
                }
                if normalized_tool == "delegate_task" and isinstance(args, Mapping):
                    event["args"] = dict(args)
                    try:
                        from tools.delegate_tool import _get_max_concurrent_children

                        event["concurrency"] = max_parallel_agents(_get_max_concurrent_children())
                    except Exception:
                        event["concurrency"] = 3
                enqueue_event(event)
            elif event_type == "tool.completed":
                result = kwargs.get("result")
                write_status = resolve_staged_write(
                    normalized_tool,
                    result,
                )
                event = {
                    "event": "tool.completed",
                    "run_id": run_id,
                    "timestamp": timestamp,
                    "tool": normalized_tool,
                    "duration": round(float(kwargs.get("duration") or 0), 3),
                    "error": bool(kwargs.get("is_error", False))
                    or write_status in {"rejected", "failed"},
                }
                if normalized_tool == "delegate_task":
                    event["output"] = safe_delegation_output(result)
                enqueue_event(event)
                if tool_name == "todo" and not bool(kwargs.get("is_error", False)):
                    todo_event = _todo_updated_event(result)
                    if todo_event is not None:
                        enqueue_event(
                            {
                                **todo_event,
                                "run_id": run_id,
                                "timestamp": timestamp,
                            }
                        )
            elif event_type == "tool.failed":
                enqueue_event(
                    {
                        "event": "tool.failed",
                        "run_id": run_id,
                        "timestamp": timestamp,
                        "tool": tool_name or "tool",
                        "duration": round(float(kwargs.get("duration") or 0), 3),
                        "error": True,
                    }
                )
            elif event_type == "reasoning.delta":
                enqueue_event(
                    {
                        "event": "reasoning.delta",
                        "run_id": run_id,
                        "timestamp": timestamp,
                        "delta": preview or "",
                    }
                )

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
                enqueue_event(
                    {
                        "event": "write.failed",
                        "run_id": run_id,
                        "timestamp": time.time(),
                        "tool": tool_name,
                        "subsystem": subsystem,
                        "pending_id": pending_id,
                        "status": "missing",
                    }
                )
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

                        applied = json.loads(apply_skill_pending(dict(record.get("payload") or {})))
                        success = bool(applied.get("success"))
                except Exception:
                    success = False
                if success:
                    write_approval.discard_pending(subsystem, pending_id)
                    _commit_resolved_write_result(
                        agent_ref[0],
                        tool_name,
                        pending_id,
                        applied,
                    )
                    if isinstance(raw_result, dict):
                        raw_result.clear()
                        raw_result.update(dict(applied))
                        raw_result.pop("pending_id", None)
                        raw_result["staged"] = False
                        raw_result["disposition"] = str(raw_result.get("disposition") or "applied")
                    status = "applied"
                else:
                    status = "failed"

            enqueue_event(
                {
                    "event": f"write.{status}",
                    "run_id": run_id,
                    "timestamp": time.time(),
                    "tool": tool_name,
                    "subsystem": subsystem,
                    "pending_id": pending_id,
                    "status": status,
                }
            )
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
        self._mark_agent_active(str(state["agent"]))
        yield self._sse_data(
            {
                "event": "run.started",
                "run_id": run_id,
                "session_id": conversation_id,
                "status": "started",
                "timestamp": time.time(),
                "model": model,
            }
        )
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
                    yield self._sse_data(
                        {
                            "event": "message.delta",
                            "run_id": run_id,
                            "timestamp": time.time(),
                            "delta": text,
                        }
                    )
                    continue
                if kind == "event":
                    yield self._sse_data(payload)
                    continue
                if kind == "failed":
                    raise payload

                result, usage = payload
                output = str(result.get("final_response") or "")
                if not output_chunks and output and not result.get("failed"):
                    # Some non-streaming-compatible providers can only return
                    # a final response. Preserve a usable fallback for them.
                    output_chunks.append(output)
                    yield self._sse_data(
                        {
                            "event": "message.delta",
                            "run_id": run_id,
                            "timestamp": time.time(),
                            "delta": output,
                        }
                    )
                if state["stop_requested"] or bool(result.get("interrupted")):
                    _persist_cancelled_terminal(agent_ref[0], "".join(output_chunks))
                    yield self._sse_data(
                        {
                            "event": "run.cancelled",
                            "run_id": run_id,
                            "timestamp": time.time(),
                        }
                    )
                elif result.get("failed"):
                    yield self._sse_data(
                        {
                            "event": "run.failed",
                            "run_id": run_id,
                            "timestamp": time.time(),
                            "message": public_error_message(result.get("error"), "Agent command failed"),
                        }
                    )
                else:
                    suggested_title = await title_task if title_task is not None else None
                    conversation_title = self._auto_title_conversation(
                        profile_dir,
                        conversation_id,
                        prepared.get("message"),
                        suggested_title=suggested_title,
                    )
                    yield self._sse_data(
                        {
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
                        }
                    )
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
            yield self._sse_data(
                {
                    "event": "run.failed",
                    "run_id": run_id,
                    "timestamp": time.time(),
                    "message": "agent command timed out",
                }
            )
            yield b"data: [DONE]\n\n"
        except Exception as exc:
            yield self._sse_data(
                {
                    "event": "run.failed",
                    "run_id": run_id,
                    "timestamp": time.time(),
                    "message": public_error_message(exc, "Agent command failed"),
                }
            )
            yield b"data: [DONE]\n\n"
        finally:
            if title_task is not None and not title_task.done():
                title_task.cancel()
            self._active_runs.pop(run_id, None)
            self._mark_agent_idle(str(state["agent"]))
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
                str(state.get("approval_session") or run_id) if state is not None else run_id
            )
            resolved = resolve_gateway_approval(
                approval_session,
                choice,
                bool(body.get("resolve_all", False)),
            )
        except ImportError as error:
            raise AgentAPIError(
                "Agent approval service is unavailable", code="approval_unavailable", status=503
            ) from error
        if resolved == 0:
            raise AgentAPIError(
                "run has no pending approval", code="approval_not_pending", status=409
            )
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
        return (
            self._sse_data(
                {
                    "id": chat_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "conversation_id": conversation_id,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                }
            )
            + b"data: [DONE]\n\n"
        )

    def _chat_sse_error(self, message: str) -> bytes:
        payload = json.dumps(
            {"error": {"message": public_error_message(message)}}, separators=(",", ":")
        )
        return f"event: error\ndata: {payload}\n\n".encode("utf-8")

    @staticmethod
    def _provider_error(output: str) -> str:
        """Recognize the structured provider failure Hermes may print with exit 0."""
        normalized = str(output or "").strip()
        return normalized if PROVIDER_ERROR_OUTPUT_RE.match(normalized) else ""

    def _sse_data(self, payload: Mapping[str, Any]) -> bytes:
        raw = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
        return f"data: {raw}\n\n".encode("utf-8")
