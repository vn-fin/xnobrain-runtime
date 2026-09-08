"""Conversation, sandbox, Kanban, and team SSE handlers."""

import asyncio
import json
from typing import Any

from fastapi import Request
from fastapi.responses import Response, StreamingResponse

from ..services import EXPECTED_ERRORS
from ..services.public_text import public_error_message


class StreamingHandlers:
    async def workspace_event_stream(self, request: Request) -> StreamingResponse:
        """Fan agent, workspace, and default-board updates into one SSE feed."""

        async def events():
            queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue(maxsize=64)
            stopped = asyncio.Event()

            async def publish(kind: str, payload: dict[str, Any]) -> None:
                if queue.full():
                    try:
                        queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                await queue.put((kind, payload))

            async def agent_updates() -> None:
                previous: dict[str, str] | None = None
                while not stopped.is_set():
                    snapshot = self.service.agent_activity()
                    activity = snapshot["agents"]
                    if activity != previous:
                        await publish("agent.activity", snapshot)
                        previous = dict(activity)
                    await asyncio.sleep(1)

            async def workspace_updates() -> None:
                previous = ""
                while not stopped.is_set():
                    detail = self.service.sandbox("detail")
                    encoded = json.dumps(detail, sort_keys=True, separators=(",", ":"))
                    if encoded != previous:
                        await publish("workspace.stats", detail)
                        previous = encoded
                    await asyncio.sleep(1)

            async def board_updates() -> None:
                board = "default"
                cursor = self.service.kanban.board_event_cursor(board)
                await publish("kanban.connected", {"board_slug": board, "cursor": cursor})
                while not stopped.is_set():
                    rows = self.service.kanban.board_events(board, after_id=cursor)
                    for item in rows:
                        cursor = max(cursor, int(item["id"]))
                        if str(item.get("kind") or "").lower() != "heartbeat":
                            await publish("kanban.task", item)
                    await asyncio.sleep(1)

            producers = [
                asyncio.create_task(agent_updates()),
                asyncio.create_task(workspace_updates()),
                asyncio.create_task(board_updates()),
            ]
            sequence = 0
            try:
                while True:
                    try:
                        kind, payload = await asyncio.wait_for(queue.get(), timeout=15)
                        sequence += 1
                        yield f"id: {sequence}\nevent: {kind}\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n"
                    except asyncio.TimeoutError:
                        yield ": keep-alive\n\n"
            finally:
                stopped.set()
                for producer in producers:
                    producer.cancel()
                await asyncio.gather(*producers, return_exceptions=True)

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    async def agent_activity_stream(self, request: Request) -> StreamingResponse:
        """Emit activity changes while keeping idle connections inexpensive."""

        async def events():
            sequence = 0
            previous: dict[str, str] | None = None
            last_emit = asyncio.get_running_loop().time()
            # StreamingResponse cancels this iterator when the transport closes.
            # Request.is_disconnected() can consume a stale disconnect message
            # behind reverse proxies and terminate a healthy SSE stream early.
            while True:
                snapshot = self.service.agent_activity()
                activity = snapshot["agents"]
                if activity != previous:
                    payload = json.dumps(snapshot, separators=(",", ":"))
                    yield f"id: {sequence}\nevent: activity\ndata: {payload}\n\n"
                    previous = dict(activity)
                    sequence += 1
                    last_emit = asyncio.get_running_loop().time()
                elif asyncio.get_running_loop().time() - last_emit >= 15.0:
                    yield ": keep-alive\n\n"
                    last_emit = asyncio.get_running_loop().time()
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

    async def stream(self, request: Request, body: dict[str, Any]) -> StreamingResponse:
        agent = str(request.query_params.get("agent") or "").strip()
        conversation_id = request.path_params["conversation_id"]

        async def events():
            try:
                async for chunk in self.service.stream_conversation(agent, conversation_id, body):
                    yield chunk
            except EXPECTED_ERRORS as error:
                yield f"event: error\ndata: {json.dumps({'message': public_error_message(error)})}\n\n".encode()

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

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
            while True:
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
            cursor = (
                max(0, int(raw_cursor))
                if raw_cursor
                else self.service.kanban.board_event_cursor(board)
            )
        except (TypeError, ValueError):
            cursor = 0

        async def events():
            nonlocal cursor
            connected = {"board_slug": board, "cursor": cursor}
            yield f"id: {cursor}\nevent: connected\ndata: {json.dumps(connected, separators=(',', ':'))}\n\n"
            while True:
                try:
                    rows = self.service.kanban.board_events(board, after_id=cursor)
                    for item in rows:
                        cursor = max(cursor, int(item["id"]))
                        if str(item.get("kind") or "").lower() == "heartbeat":
                            continue
                        yield f"id: {cursor}\nevent: task\ndata: {json.dumps(item, separators=(',', ':'))}\n\n"
                except EXPECTED_ERRORS as error:
                    yield f"event: error\ndata: {json.dumps({'message': public_error_message(error)})}\n\n"
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
            while True:
                try:
                    record = runs.get_run(team_id, run_id)
                except EXPECTED_ERRORS as error:
                    yield f"event: error\ndata: {json.dumps({'message': public_error_message(error)})}\n\n"
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

    async def conversation_run_event_stream(self, request: Request) -> StreamingResponse:
        """Replay persisted chat events, then follow the detached run live."""
        agent_id = str(request.query_params.get("agent") or "").strip()
        conversation_id = request.path_params["conversation_id"]
        run_id = request.path_params["run_id"]
        raw_cursor = request.headers.get("Last-Event-ID") or request.query_params.get("after")
        try:
            cursor = max(0, int(raw_cursor)) if raw_cursor else 0
        except (TypeError, ValueError):
            cursor = 0

        async def events():
            try:
                async for item in self.service.conversation_runs.events(
                    agent_id,
                    conversation_id,
                    run_id,
                    cursor,
                ):
                    sequence = int(item.get("sequence") or 0)
                    name = str(item.get("event") or "message")
                    payload = json.dumps(
                        item.get("data"), ensure_ascii=False, separators=(",", ":")
                    )
                    yield f"id: {sequence}\nevent: {name}\ndata: {payload}\n\n".encode()
                yield b"data: [DONE]\n\n"
            except EXPECTED_ERRORS as error:
                yield f"event: error\ndata: {json.dumps({'message': public_error_message(error)})}\n\n".encode()

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
