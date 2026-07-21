"""FastAPI handlers layered over the Open Lumora platform service."""

from __future__ import annotations

import inspect
import json
import os
import time
from typing import Any, Callable

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from ..services import EXPECTED_ERRORS, PlatformService


class APIHandlers:
    """Expose the stable Open Lumora contract without duplicating Hermes APIs."""

    def __init__(self, service: PlatformService):
        self.service = service
        self.started_at = time.time()
        self.enterprise_url = str(os.getenv("ENTERPRISE_API_URL") or "").rstrip("/")

    @staticmethod
    def success(data: Any, message: str = "ok", status: int = 200) -> JSONResponse:
        return JSONResponse({"success": True, "data": data, "message": message, "status_code": status}, status_code=status)

    @staticmethod
    def failure(error: Exception) -> JSONResponse:
        status = int(getattr(error, "status", 500))
        return JSONResponse({"success": False, "message": str(error), "error": {"code": str(getattr(error, "code", "internal_error"))}, "status_code": status}, status_code=status)

    async def dispatch(self, request: Request, body: dict[str, Any]) -> Response:
        name = request.scope["route"].name
        try:
            operation, message, status = self._operation(name, request, body)
            result = operation()
            if inspect.isawaitable(result):
                result = await result
            return self.success(result, message, status)
        except EXPECTED_ERRORS as error:
            return self.failure(error)
        except (ValueError, KeyError, TypeError) as error:
            if not hasattr(error, "status"):
                error.status, error.code = 400, "invalid_request"
            return self.failure(error)

    def _operation(self, name: str, request: Request, body: dict[str, Any]) -> tuple[Callable[[], Any], str, int]:
        p, q, s = request.path_params, request.query_params, self.service
        agent = lambda: str(q.get("agent") or "").strip() or (_ for _ in ()).throw(ValueError("agent is required"))
        operations: dict[str, tuple[Callable[[], Any], str, int]] = {
            "health": (lambda: {"status": "ok", "edition": "opensource", "api": "fastapi", "uptime_seconds": int(time.time()-self.started_at)}, "healthy", 200),
            "limits": (lambda: {"plan_id": "self-hosted", "local_features_unlimited": True, "agents": -1, "teams": -1, "mcp_servers": -1, "cron_jobs": -1}, "limits retrieved", 200),
            "deployment": (lambda: {"mode": os.getenv("START_MODE", "local"), "start_mode": os.getenv("START_MODE", "local"), "runtime": "hermes-fastapi", "runtime_transport": "in-process", "gateway_configured": bool(self.enterprise_url), "managed_cloud": bool(self.enterprise_url), "enterprise_api_url": self.enterprise_url, "enterprise_connected": bool(self.enterprise_url), "database": False}, "deployment retrieved", 200),
            "enterprise_features": (lambda: {"available": bool(self.enterprise_url), "local_features_unrestricted": True}, "features retrieved", 200),
            "device_status": (self._device_status, "device status retrieved successfully", 200),
            "device_pair": (lambda: self._device_action("pair"), "device pairing started", 202),
            "device_unpair": (lambda: self._device_action("unpair"), "device unpaired successfully", 200),
            "agents_list": (s.list_agents, "agents retrieved successfully", 200), "agents_create": (lambda: s.create_agent(body), "agent created successfully", 201),
            "profiles_list": (s.list_profiles, "profiles retrieved successfully", 200),
            "agents_get": (lambda: s.get_agent(p["agent_id"]), "agent retrieved successfully", 200),
            "agents_metadata": (lambda: s.update_agent_metadata(p["agent_id"], body), "agent updated successfully", 200),
            "agents_delete": (lambda: s.delete_agent(p["agent_id"]), "agent deleted successfully", 200),
            "agents_test": (lambda: {"agent_id": s.get_agent(p["agent_id"])["id"], "healthy": True, "status": "ok"}, "agent tested successfully", 200),
            "config_global_get": (s.global_config, "global config retrieved successfully", 200),
            "config_global_patch": (lambda: s.update_global_config(body), "global config updated successfully", 200),
            "config_agent_patch": (lambda: s.update_agent_config(p["agent_id"], body), "agent config updated successfully", 200),
            "skills_default_list": (s.list_default_skills, "default profile skills retrieved successfully", 200),
            "skills_list": (lambda: s.list_skills(p["agent_id"]), "skills retrieved successfully", 200),
            "skills_install": (lambda: s.install_skill(p["agent_id"], body), "skill installed successfully", 201),
            "skills_patch": (lambda: s.set_skill_enabled(p["agent_id"], p["skill_id"], body), "skill updated successfully", 200),
            "skills_delete": (lambda: s.remove_skill(p["agent_id"], p["skill_id"]), "skill removed successfully", 200),
            "memory_get": (lambda: s.read_memory(p["agent_id"]), "memory retrieved successfully", 200),
            "memory_patch": (lambda: s.write_memory(p["agent_id"], body), "memory updated successfully", 200),
            "snapshots_list": (lambda: s.list_snapshots(p["agent_id"], q.get("kind")), "snapshots retrieved successfully", 200),
            "snapshots_restore": (lambda: s.restore_snapshot(p["agent_id"], p["snapshot_id"]), "snapshot restored successfully", 200),
            "workspace_list": (lambda: s.list_workspace(p["agent_id"], q.get("path", ".")), "workspace retrieved successfully", 200),
            "workspace_view": (lambda: s.read_workspace(p["agent_id"], {"path": q.get("path")}), "file retrieved successfully", 200),
            "workspace_read": (lambda: s.read_workspace(p["agent_id"], body), "file retrieved successfully", 200),
            "workspace_write": (lambda: s.write_workspace(p["agent_id"], body), "workspace updated successfully", 200),
            "workspace_create": (lambda: s.create_workspace(p["agent_id"], body), "workspace created successfully", 201),
            "workspace_delete": (lambda: s.delete_workspace(p["agent_id"], body), "workspace deleted successfully", 200),
            "mcp_get": (lambda: s.get_mcp(p["agent_id"]), "MCP config retrieved successfully", 200),
            "mcp_put": (lambda: s.update_mcp(p["agent_id"], body), "MCP config updated successfully", 200),
            "conversations_list": (lambda: s.list_conversations(agent()), "conversations retrieved successfully", 200),
            "conversations_create": (lambda: s.create_conversation(agent(), body), "conversation created successfully", 201),
            "conversations_get": (lambda: s.get_conversation(agent(), p["conversation_id"]), "conversation retrieved successfully", 200),
            "messages_list": (lambda: {"messages": s.get_conversation(agent(), p["conversation_id"])["messages"]}, "messages retrieved successfully", 200),
            "conversations_usage": (lambda: {"conversation_id": p["conversation_id"], "tokens": {"total": 0}, "cost": {"total_usd": 0}}, "usage retrieved successfully", 200),
            "conversations_rename": (lambda: s.rename_conversation(agent(), p["conversation_id"], body), "conversation renamed successfully", 200),
            "conversations_delete": (lambda: s.delete_conversation(agent(), p["conversation_id"]), "conversation deleted successfully", 200),
            "run_stop": (lambda: s.stop_run(p["run_id"]), "run stopped successfully", 200),
            "run_approval": (lambda: s.resolve_approval(p["run_id"], body, agent()), "approval resolved successfully", 200),
            "bundle_export_start": (lambda: s.start_bundle_export(body), "bundle export prepared", 201),
            "bundle_export_delete": (lambda: s.delete_bundle_transfer("export", p["transfer_id"]), "bundle export deleted", 200),
            "bundle_upload_start": (lambda: s.start_bundle_upload(body), "bundle upload created", 201),
            "bundle_upload_complete": (lambda: s.complete_bundle_upload(p["transfer_id"], body), "bundle upload completed", 200),
            "bundle_upload_apply": (lambda: s.apply_bundle_upload(p["transfer_id"], body), "bundle imported successfully", 201),
            "bundle_upload_delete": (lambda: s.delete_bundle_transfer("upload", p["transfer_id"]), "bundle upload deleted", 200),
            "cron_list": (s.list_crons, "cron jobs retrieved successfully", 200), "cron_create": (lambda: s.create_cron(body), "cron job created successfully", 201),
            "cron_pause": (lambda: s.set_cron_enabled(p["job_id"], False), "cron job paused", 200), "cron_resume": (lambda: s.set_cron_enabled(p["job_id"], True), "cron job resumed", 200),
            "cron_run": (lambda: s.run_cron(p["job_id"]), "cron job completed", 200), "cron_delete": (lambda: s.delete_cron(p["job_id"]), "cron job deleted", 200),
            "notifications": (s.repository.list_notifications, "notifications retrieved successfully", 200),
            "notification_resolve": (lambda: s.repository.resolve_notification(p["notification_id"]), "notification resolved successfully", 200),
            "teams_list": (s.list_teams, "teams retrieved successfully", 200), "teams_create": (lambda: s.create_team(body), "team created successfully", 201),
            "teams_get": (lambda: s.get_team(p["team_id"]), "team retrieved successfully", 200), "teams_update": (lambda: s.update_team(p["team_id"], body), "team updated successfully", 200),
            "teams_delete": (lambda: s.delete_team(p["team_id"]), "team deleted successfully", 200), "teams_run": (lambda: s.run_team(p["team_id"], body), "team run completed successfully", 200),
            "providers": (s.providers, "providers retrieved successfully", 200),
            "provider_connect_start": (lambda: s.start_provider_connect(p["provider_id"]), "provider connection started", 200),
            "provider_connect_status": (lambda: s.provider_status(p["provider_id"]), "provider status retrieved", 200),
            "provider_connect_submit": (lambda: s.submit_provider_connect(p["provider_id"], body), "provider connected", 200),
            "provider_update": (lambda: s.update_provider(p["provider_id"], body), "provider updated", 200),
            "provider_disconnect": (lambda: s.disconnect_provider(p["provider_id"]), "provider disconnected", 200),
            "provider_test": (lambda: s.test_provider(p["provider_id"]), "provider tested", 200),
            "provider_models": (lambda: self._provider_models(p["provider_id"]), "models retrieved successfully", 200),
            "provider_reasoning": (lambda: {"provider_id": p["provider_id"], "model": p["model"], "reasoning": ["low", "medium", "high"]}, "reasoning options retrieved", 200),
            "sandbox": (lambda: {"status": "ok", "runtime": "hermes", "single_api_server": True, "action": p["action"]}, "sandbox status retrieved", 200),
        }
        if name not in operations:
            raise ValueError("unsupported route")
        return operations[name]

    async def _provider_models(self, provider: str) -> dict[str, Any]:
        items = (await self.service.router.list_models())["data"]
        return {"provider_id": provider, "default_model": "auto", "models": [{"id": "auto", "reasoning": ["low", "medium", "high"]}] + [{"id": x["id"], "reasoning": ["low", "medium", "high"]} for x in items if x.get("provider") == provider]}

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

    async def workspace_upload(self, request: Request) -> Response:
        try:
            form = await request.form()
            upload = form.get("file")
            if upload is None or not hasattr(upload, "read"):
                raise ValueError("file is required")
            content = await upload.read()
            if hasattr(upload, "close"):
                await upload.close()
            result = self.service.write_workspace(request.path_params["agent_id"], {
                "path": str(form.get("path") or getattr(upload, "filename", "upload.bin")),
                "content_base64": __import__("base64").b64encode(content).decode("ascii"),
            })
            return self.success(result, "file uploaded successfully", 201)
        except EXPECTED_ERRORS as error:
            return self.failure(error)
        except ValueError as error:
            error.status, error.code = 400, "invalid_request"
            return self.failure(error)

    async def enterprise_proxy(self, request: Request) -> Response:
        if not self.enterprise_url:
            error = ValueError("ENTERPRISE_API_URL is not configured"); error.status, error.code = 503, "enterprise_unavailable"
            return self.failure(error)
        headers = {key: value for key, value in request.headers.items() if key.lower() in {"authorization", "traceparent", "tracestate"}}
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.request(request.method, self.enterprise_url + request.url.path + (f"?{request.url.query}" if request.url.query else ""), headers=headers)
            return Response(response.content, status_code=response.status_code, media_type=response.headers.get("content-type"))
        except httpx.HTTPError:
            error = ValueError("Enterprise API is unavailable"); error.status, error.code = 503, "enterprise_unavailable"
            return self.failure(error)

    async def bundle_export(self, _request: Request, body: dict[str, Any]) -> Response:
        try:
            payload, filename = self.service.export_bundle(body)
            return Response(
                payload,
                media_type="application/vnd.open-lumora.bundle",
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )
        except EXPECTED_ERRORS as error:
            return self.failure(error)

    async def bundle_upload(self, request: Request) -> Response:
        try:
            form = await request.form()
            upload = form.get("file")
            if upload is None or not hasattr(upload, "read"):
                raise ValueError("bundle file is required")
            payload = await upload.read()
            if hasattr(upload, "close"):
                await upload.close()
            operation = request.scope["route"].name
            if operation == "bundle_inspect":
                result, message, status = self.service.inspect_bundle(payload), "bundle inspected successfully", 200
            elif operation == "bundle_dry_run":
                result, message, status = self.service.dry_run_bundle(payload), "bundle dry run completed successfully", 200
            else:
                result, message, status = self.service.apply_bundle(payload), "bundle imported successfully", 201
            return self.success(result, message, status)
        except EXPECTED_ERRORS as error:
            return self.failure(error)
        except (ValueError, KeyError, TypeError) as error:
            error.status, error.code = 400, "invalid_bundle"
            return self.failure(error)

    async def bundle_part(self, request: Request) -> Response:
        try:
            transfer_id = request.path_params["transfer_id"]
            part_number = request.path_params["part_number"]
            operation = request.scope["route"].name
            if operation == "bundle_upload_part":
                content_length = int(request.headers.get("content-length") or 0)
                if content_length <= 0 or content_length > 4 * 1024 * 1024:
                    raise ValueError("upload part size is invalid")
                payload = await request.body()
                expected_hash = str(request.headers.get("x-part-sha256") or "").lower()
                if expected_hash and expected_hash != __import__("hashlib").sha256(payload).hexdigest():
                    raise ValueError("upload part checksum is invalid")
                result = self.service.put_bundle_upload_part(transfer_id, part_number, payload)
                return self.success(result, "bundle part uploaded", 201)
            payload, metadata = self.service.bundle_export_part(transfer_id, part_number)
            return Response(
                payload,
                media_type="application/octet-stream",
                headers={
                    "Content-Disposition": f'attachment; filename="{metadata["filename"]}.part{int(part_number):08d}"',
                    "X-Transfer-Filename": metadata["filename"],
                    "X-Part-Number": str(part_number),
                    "X-Total-Parts": str(metadata["total_parts"]),
                    "X-Part-SHA256": __import__("hashlib").sha256(payload).hexdigest(),
                },
            )
        except EXPECTED_ERRORS as error:
            return self.failure(error)
        except (ValueError, KeyError, TypeError) as error:
            error.status, error.code = 400, "invalid_bundle_part"
            return self.failure(error)

    async def sandbox_setup(self, request: Request) -> Response:
        payload = {"status": "ready", "runtime": "container", "provisioned": True}
        if request.query_params.get("stream") == "true":
            async def progress():
                for value in (10, 35, 70, 100):
                    yield f"data: {value}\n\n"
            return StreamingResponse(progress(), media_type="text/event-stream")
        return self.success(payload, "sandbox ready")

    def _device_status(self) -> dict[str, Any]:
        return {
            "enabled": bool(self.enterprise_url), "connected": False,
            "endpoint": self.enterprise_url, "queued_commands": 0, "claimed": False,
        }

    async def _device_action(self, action: str) -> dict[str, Any]:
        if not self.enterprise_url:
            if action == "unpair":
                return {"unpaired": True}
            error = ValueError("ENTERPRISE_API_URL is not configured")
            error.status, error.code = 503, "enterprise_unavailable"
            raise error
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(f"{self.enterprise_url}/api/v1/device/{action}")
        if response.status_code >= 400:
            error = ValueError("Enterprise device API rejected the request")
            error.status, error.code = response.status_code, "enterprise_error"
            raise error
        return response.json().get("data", response.json())
