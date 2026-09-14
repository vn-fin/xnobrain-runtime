"""OpenAI-compatible agent API translation."""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse, StreamingResponse

from ..services import EXPECTED_ERRORS
from ..services.public_text import public_error_message
from ..trusted_context import from_request


class AgentAPIHandlers:
    """Return OpenAI wire responses without the XNOBrain JSON envelope."""

    async def agent_chat_completions(
        self,
        request: Request,
        body: dict[str, Any],
    ):
        agent_id = str(request.path_params.get("agent_id") or "").strip()
        trusted = from_request(request)
        if not trusted.subject:
            return self._agent_api_error(
                401, "Authentication is required.", "authentication_required"
            )
        try:
            if body.get("stream") is True:

                async def events():
                    try:
                        async for chunk in self.service.agent_api.stream(
                            agent_id,
                            body,
                            trusted,
                        ):
                            yield chunk
                    except EXPECTED_ERRORS as error:
                        yield self._agent_api_stream_error(
                            public_error_message(error),
                            str(getattr(error, "code", "agent_completion_failed")),
                        )
                    except Exception:
                        yield self._agent_api_stream_error(
                            "Agent completion failed.",
                            "agent_completion_failed",
                        )

                return StreamingResponse(
                    events(),
                    media_type="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache, no-transform",
                        "X-Accel-Buffering": "no",
                    },
                )
            data = await self.service.agent_api.completion(agent_id, body, trusted)
            return JSONResponse(data, headers={"Cache-Control": "private, no-store"})
        except EXPECTED_ERRORS as error:
            return self._agent_api_error(
                int(getattr(error, "status", 500)),
                public_error_message(error),
                str(getattr(error, "code", "agent_completion_failed")),
            )

    @staticmethod
    def _agent_api_stream_error(message: str, code: str) -> bytes:
        import json

        payload = {
            "error": {
                "message": message,
                "type": "api_error",
                "code": code,
            }
        }
        return f"data: {json.dumps(payload, separators=(',', ':'))}\n\ndata: [DONE]\n\n".encode()

    @staticmethod
    def _agent_api_error(status: int, message: str, code: str) -> JSONResponse:
        error_type = "invalid_request_error" if status < 500 else "api_error"
        return JSONResponse(
            {"error": {"message": message, "type": error_type, "code": code}},
            status_code=status,
            headers={"Cache-Control": "private, no-store"},
        )
