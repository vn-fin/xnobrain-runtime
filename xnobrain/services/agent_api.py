"""OpenAI-compatible adapter over durable XNOBrain agent runs."""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator, Mapping
from typing import Any

from .base import ServiceError


class AgentAPIService:
    """Invoke one owned agent while preserving normal budget and run controls."""

    def __init__(self, platform: Any):
        self.platform = platform

    @staticmethod
    def _content_text(content: Any) -> str:
        if isinstance(content, str):
            return content.strip()
        if not isinstance(content, list):
            return ""
        parts: list[str] = []
        for item in content:
            if not isinstance(item, Mapping):
                continue
            if str(item.get("type") or "") not in {"", "text", "input_text", "output_text"}:
                continue
            value = item.get("text")
            if isinstance(value, Mapping):
                value = value.get("value")
            text = str(value or "").strip()
            if text:
                parts.append(text)
        return "\n".join(parts)

    def _input(self, body: Mapping[str, Any]) -> str:
        transcript: list[str] = []
        for message in body.get("messages") or []:
            if not isinstance(message, Mapping):
                continue
            content = self._content_text(message.get("content"))
            if not content:
                continue
            role = str(message.get("role") or "user").upper()
            name = str(message.get("name") or "").strip()
            label = f"{role} ({name})" if name else role
            transcript.append(f"{label}:\n{content}")
        if not transcript:
            raise ServiceError(
                "messages must contain text content",
                status=422,
                code="invalid_messages",
            )
        return "\n\n".join(transcript)

    @staticmethod
    def _payload(event: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
        raw = event.get("data")
        payload = dict(raw) if isinstance(raw, Mapping) else {}
        name = str(payload.get("event") or event.get("event") or "message")
        return name, payload

    @staticmethod
    def _usage(payload: Mapping[str, Any]) -> dict[str, int]:
        source = payload.get("usage")
        usage = dict(source) if isinstance(source, Mapping) else {}

        def integer(*names: str) -> int:
            for name in names:
                try:
                    return max(0, int(usage.get(name) or 0))
                except (TypeError, ValueError):
                    continue
            return 0

        prompt = integer("input_tokens", "prompt_tokens")
        completion = integer("output_tokens", "completion_tokens")
        total = integer("total_tokens") or prompt + completion
        return {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": total,
        }

    async def _start(
        self,
        agent_id: str,
        body: Mapping[str, Any],
        trusted_context: Any,
    ) -> tuple[str, dict[str, Any]]:
        model = str(body.get("model") or "").strip()
        if model != agent_id:
            raise ServiceError(
                "model must match the agent ID in the endpoint",
                status=404,
                code="agent_model_not_found",
            )
        self.platform.get_agent(agent_id)
        prompt = self._input(body)
        conversation = self.platform.create_conversation(
            agent_id,
            {"title": "API request"},
            trusted_context,
        )
        conversation_id = str(conversation["id"])
        run_body = {
            "input": prompt,
            "run_mode": "interactive",
        }
        run = await self.platform.start_conversation_run(
            agent_id,
            conversation_id,
            run_body,
            trusted_context,
        )
        return conversation_id, run

    async def completion(
        self,
        agent_id: str,
        body: Mapping[str, Any],
        trusted_context: Any,
    ) -> dict[str, Any]:
        conversation_id, run = await self._start(agent_id, body, trusted_context)
        output: list[str] = []
        terminal: dict[str, Any] = {}
        async for event in self.platform.conversation_runs.events(
            agent_id,
            conversation_id,
            str(run["id"]),
        ):
            name, payload = self._payload(event)
            if name == "message.delta":
                output.append(str(payload.get("delta") or ""))
            elif name == "run.completed":
                terminal = payload
            elif name == "run.failed":
                raise ServiceError(
                    "agent completion failed",
                    status=502,
                    code="agent_completion_failed",
                )
            elif name == "run.cancelled":
                raise ServiceError(
                    "agent completion was cancelled",
                    status=409,
                    code="agent_completion_cancelled",
                )
        content = str(terminal.get("output") or "") or "".join(output)
        return {
            "id": f"chatcmpl-{run['id']}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": agent_id,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": self._usage(terminal),
            "xnobrain": {
                "agent_id": agent_id,
                "conversation_id": conversation_id,
                "run_id": str(run["id"]),
            },
        }

    async def stream(
        self,
        agent_id: str,
        body: Mapping[str, Any],
        trusted_context: Any,
    ) -> AsyncIterator[bytes]:
        conversation_id, run = await self._start(agent_id, body, trusted_context)
        completion_id = f"chatcmpl-{run['id']}"
        created = int(time.time())
        first = True
        async for event in self.platform.conversation_runs.events(
            agent_id,
            conversation_id,
            str(run["id"]),
        ):
            name, payload = self._payload(event)
            if name == "message.delta":
                delta: dict[str, str] = {"content": str(payload.get("delta") or "")}
                if first:
                    delta["role"] = "assistant"
                    first = False
                yield self._sse_chunk(completion_id, created, agent_id, delta, None)
            elif name == "run.completed":
                yield self._sse_chunk(completion_id, created, agent_id, {}, "stop")
            elif name in {"run.failed", "run.cancelled"}:
                error = {
                    "error": {
                        "message": "Agent completion did not finish successfully.",
                        "type": "agent_completion_error",
                        "code": "agent_completion_failed",
                    }
                }
                yield f"data: {json.dumps(error, separators=(',', ':'))}\n\n".encode()
        yield b"data: [DONE]\n\n"

    @staticmethod
    def _sse_chunk(
        completion_id: str,
        created: int,
        model: str,
        delta: Mapping[str, Any],
        finish_reason: str | None,
    ) -> bytes:
        payload = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": dict(delta),
                    "finish_reason": finish_reason,
                }
            ],
        }
        return f"data: {json.dumps(payload, separators=(',', ':'))}\n\n".encode()
