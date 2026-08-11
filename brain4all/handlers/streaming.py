"""Conversation, sandbox, Kanban, and team SSE handlers."""

import asyncio
import json
from typing import Any

from fastapi import Request
from fastapi.responses import Response, StreamingResponse

from ..services import EXPECTED_ERRORS


class StreamingHandlers:

    async def stream(self, request: Request, body: dict[str, Any]) -> StreamingResponse:
            agent = str(request.query_params.get("agent") or "").strip()
            conversation_id = request.path_params["conversation_id"]
            async def events():
                try:
                    async for chunk in self.service.stream_conversation(agent, conversation_id, body):
                        yield chunk
                except EXPECTED_ERRORS as error:
                    yield f"event: error\ndata: {json.dumps({'message': str(error)})}\n\n".encode()
            return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    async def sandbox_setup(self, request: Request) -> Response:
            payload = {"status": "ready", "runtime": "container", "provisioned": True}
            if request.query_params.get("stream") == "true":
                async def progress():
                    for value in (10, 35, 70, 100):
                        yield f"data: {value}\n\n"
                return StreamingResponse(progress(), media_type="text/event-stream")
            return self.success(payload, "sandbox ready")

    async def sandbox_detail_stream(self, request: Request) -> StreamingResponse:
            """Stream one important runtime snapshot per second until disconnect."""
            async def events():
                sequence = 0
                while not await request.is_disconnected():
                    detail = self.service.sandbox("detail")
                    payload = json.dumps(detail, separators=(",", ":"))
                    yield f"id: {sequence}\nevent: stats\ndata: {payload}\n\n"
                    sequence += 1
                    await asyncio.sleep(1)
    
            return StreamingResponse(
                events(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache, no-transform",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )

    async def kanban_event_stream(self, request: Request) -> StreamingResponse:
            """Stream new board task events without replaying history by default."""
            board = request.path_params["board_slug"]
            raw_cursor = request.headers.get("Last-Event-ID") or request.query_params.get("after")
            try:
                cursor = max(0, int(raw_cursor)) if raw_cursor else self.service.kanban.board_event_cursor(board)
            except (TypeError, ValueError):
                cursor = 0
    
            async def events():
                nonlocal cursor
                connected = {"board_slug": board, "cursor": cursor}
                yield f"id: {cursor}\nevent: connected\ndata: {json.dumps(connected, separators=(',', ':'))}\n\n"
                while not await request.is_disconnected():
                    try:
                        rows = self.service.kanban.board_events(board, after_id=cursor)
                        for item in rows:
                            cursor = max(cursor, int(item["id"]))
                            if str(item.get("kind") or "").lower() == "heartbeat":
                                continue
                            yield f"id: {cursor}\nevent: task\ndata: {json.dumps(item, separators=(',', ':'))}\n\n"
                    except EXPECTED_ERRORS as error:
                        yield f"event: error\ndata: {json.dumps({'message': str(error)})}\n\n"
                        return
                    await asyncio.sleep(1)
    
            return StreamingResponse(
                events(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache, no-transform",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )

    async def team_run_event_stream(self, request: Request) -> StreamingResponse:
            """Stream a team run's transitions from the persisted record, waking on the
            registry's in-memory pulse so live steps surface without the poll latency."""
            team_id = request.path_params["team_id"]
            run_id = request.path_params["run_id"]
            runs = self.service.team_runs
            raw_cursor = request.headers.get("Last-Event-ID") or request.query_params.get("after")
            try:
                cursor = max(0, int(raw_cursor)) if raw_cursor else 0
            except (TypeError, ValueError):
                cursor = 0
    
            async def events():
                nonlocal cursor
                connected = {"run_id": run_id, "team_id": team_id, "revision": cursor}
                yield f"id: {cursor}\nevent: connected\ndata: {json.dumps(connected, separators=(',', ':'))}\n\n"
                while not await request.is_disconnected():
                    try:
                        record = runs.get_run(team_id, run_id)
                    except EXPECTED_ERRORS as error:
                        yield f"event: error\ndata: {json.dumps({'message': str(error)})}\n\n"
                        return
                    revision = int(record.get("revision", 0))
                    if revision > cursor:
                        cursor = revision
                        payload = json.dumps(runs.sanitized(record), separators=(",", ":"))
                        yield f"id: {cursor}\nevent: run\ndata: {payload}\n\n"
                    if record.get("status") in {"completed", "failed", "cancelled"}:
                        done = {"run_id": run_id, "status": record["status"]}
                        yield f"id: {cursor}\nevent: done\ndata: {json.dumps(done, separators=(',', ':'))}\n\n"
                        return
                    entry = runs.registry_entry(run_id)
                    if entry is not None:
                        try:
                            await asyncio.wait_for(entry.changed.wait(), timeout=1.0)
                        except asyncio.TimeoutError:
                            pass
                    else:
                        await asyncio.sleep(1)
    
            return StreamingResponse(
                events(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache, no-transform",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )
