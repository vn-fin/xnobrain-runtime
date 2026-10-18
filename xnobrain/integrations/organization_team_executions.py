"""Run-scoped Control protocol for remote member workers."""

import asyncio
import re
import uuid

import httpx

from ..services.base import ServiceError


class OrganizationTeamExecutionClient:
    def __init__(self, endpoint, organization_id, run_id, capability):
        self.url = (
            endpoint.rstrip("/")
            + f"/xnobrain/api/control/v1/organizations/{organization_id}"
            + f"/agent-team-executions/{run_id}"
        )
        self.headers = {"Authorization": f"Bearer {capability}"}

    async def request(self, method, suffix, body=None):
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.request(
                    method, self.url + suffix, headers=self.headers, json=body
                )
                message = ""
                if response.status_code in {409, 422}:
                    try:
                        envelope = response.json()
                    except ValueError:
                        envelope = {}
                    if isinstance(envelope, dict) and isinstance(envelope.get("message"), str):
                        message = envelope["message"]
                if response.status_code in {403, 409}:
                    if (
                        response.status_code == 409
                        and message == "organization_funding_unavailable"
                    ):
                        raise ServiceError(message, status=409, code=message)
                    raise ServiceError(
                        "organization execution revoked",
                        status=403,
                        code="organization_execution_revoked",
                    )
                if response.status_code == 422:
                    code = (
                        message
                        if len(message) <= 100
                        and re.fullmatch(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*", message)
                        else "organization_execution_invalid"
                    )
                    raise ServiceError(
                        message or "organization execution invalid", status=422, code=code
                    )
                response.raise_for_status()
                return response.json()["data"]
        except ServiceError:
            raise
        except (httpx.HTTPError, ValueError, KeyError) as error:
            raise ServiceError(
                "organization execution unavailable",
                status=503,
                code="organization_execution_unavailable",
            ) from error

    async def record(self, record):
        await self.request("PUT", "/record", record)

    async def chat(self, agent_id, request):
        work = await self.request(
            "POST",
            "/workers",
            {
                "call_id": uuid.uuid4().hex,
                "agent_id": agent_id,
                "message": request["message"],
                "model": request.get("model"),
                "toolsets": request.get("toolsets", []),
                "skills": request.get("skills", []),
            },
        )
        worker_id = work["id"]
        finished = False
        try:
            async with asyncio.timeout(30 * 60):
                while True:
                    work = await self.request("GET", f"/workers/{worker_id}")
                    steps = work.get("steps") or []
                    if steps and steps[0]["status"] == "completed":
                        finished = True
                        # Remote conversation IDs cannot be opened in this workspace.
                        return {"response": steps[0].get("safe_summary", {}).get("output", "")}
                    if work["status"] in {"failed", "cancelled", "cancel_requested"} or (
                        steps and steps[0]["status"] in {"failed", "cancelled"}
                    ):
                        finished = True
                        # Keep the member Runtime's reason so the team step shows it.
                        reason = str((steps[0].get("error_code") if steps else "") or "")
                        raise ServiceError(
                            "member worker failed", code=reason or "member_worker_failed"
                        )
                    await asyncio.sleep(1)
        finally:
            if not finished:
                try:
                    await asyncio.shield(self.request("DELETE", f"/workers/{worker_id}"))
                except ServiceError:
                    pass  # Control also cancels all workers on the final run callback.
