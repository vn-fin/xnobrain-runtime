"""Bounded child-profile execution adapter for Agent Maker certification."""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol


@dataclass(frozen=True)
class CertificationExecution:
    """Safe evidence returned by one child-profile certification turn."""

    response: str
    observed_tools: tuple[str, ...]
    usage: Mapping[str, int]
    cost_usd: float
    terminal_status: str = "completed"


class CertificationExecutor(Protocol):
    async def execute(
        self,
        *,
        profile_id: str,
        work_context_id: str,
        ownership_context: Mapping[str, Any],
        prompt: str,
        allowed_tools: tuple[str, ...],
        max_turns: int,
        timeout_seconds: float,
        session_id: str,
    ) -> CertificationExecution: ...


class RuntimeCertificationExecutor:
    """Execute certification through the normal profile/session/model/tool path."""

    def __init__(
        self,
        agents: Any,
        conversation_creator: Callable[[str, Mapping[str, Any]], Mapping[str, Any]],
    ):
        self.agents = agents
        self.conversation_creator = conversation_creator

    async def execute(
        self,
        *,
        profile_id: str,
        work_context_id: str,
        ownership_context: Mapping[str, Any],
        prompt: str,
        allowed_tools: tuple[str, ...],
        max_turns: int,
        timeout_seconds: float,
        session_id: str,
    ) -> CertificationExecution:
        # A fresh persisted child session is used for every case. The embedded
        # runner creates one AIAgent per call, enters the selected profile home,
        # loads its SOUL/AGENTS/config/skills/memory, and applies the tool allowlist.
        conversation_body: dict[str, Any] = {
            "id": session_id,
            "title": "Agent Maker certification",
        }
        if work_context_id != "personal":
            conversation_body["ownership_context"] = dict(ownership_context)
        conversation = self.conversation_creator(profile_id, conversation_body)
        session_id = str(conversation.get("id") or session_id)
        observed: list[str] = []

        def progress(event: str, tool: str | None = None, *_args: Any, **_kwargs: Any) -> None:
            if event in {"tool.started", "tool.completed", "tool.failed"} and tool:
                if tool not in observed:
                    observed.append(tool)

        prepared = self.agents._prepare_chat_command(
            profile_id,
            {
                "message": prompt,
                "conversation_id": session_id,
                "toolsets": list(allowed_tools),
                "timeout_seconds": max(1, int(timeout_seconds)),
                "ownership_context": dict(ownership_context),
            },
            require_conversation=True,
        )
        started = time.monotonic()
        result, usage = await asyncio.wait_for(
            self.agents._run_session_agent(
                prepared,
                run_id="run_" + uuid.uuid4().hex,
                stream_delta_callback=lambda _delta: None,
                tool_progress_callback=progress,
                approval_notify_callback=lambda _request: None,
                agent_ref=[None],
                max_iterations=max_turns,
            ),
            timeout=max(0.1, timeout_seconds),
        )
        status = "failed" if result.get("failed") else "completed"
        normalized_usage = {
            key: max(0, int(usage.get(key) or 0))
            for key in ("input_tokens", "output_tokens", "total_tokens")
        }
        normalized_usage["duration_ms"] = max(0, int((time.monotonic() - started) * 1000))
        return CertificationExecution(
            response=str(result.get("final_response") or ""),
            observed_tools=tuple(observed),
            usage=normalized_usage,
            # GoRouter is authoritative for paid accounting. A concrete cost is
            # optional here; the service still enforces the caller's aggregate cap.
            cost_usd=max(0.0, float(usage.get("cost_usd") or 0.0)),
            terminal_status=status,
        )
