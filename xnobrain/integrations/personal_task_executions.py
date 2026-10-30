"""Runtime-to-Control transport for owner-authorized personal task admission."""

import os
from urllib.parse import quote

import httpx

from ..services.base import ServiceError


class PersonalTaskExecutionClient:
    def __init__(self, client=None):
        self.client = client or httpx.AsyncClient(timeout=15, follow_redirects=False)

    async def close(self):
        await self.client.aclose()

    async def call(self, method, execution_id="", *, body=None, key=None):
        endpoint = os.getenv("RUNTIME_CONTROL_URL", "").rstrip("/")
        workspace = os.getenv("RUNTIME_WORKSPACE_ID", "").strip()
        secret = os.getenv("RUNTIME_INTERNAL_SERVICE_TOKEN", "").strip()
        if not endpoint or not workspace or not secret:
            raise ServiceError("Task execution service is unavailable", status=503)
        path = "/xnobrain/api/control/internal/v1/personal/delegated-executions"
        if execution_id:
            path += "/" + quote(execution_id, safe="")
        headers = {"Authorization": "Bearer " + secret, "X-XNOBrain-Workspace-ID": workspace}
        if key:
            headers["Idempotency-Key"] = key
        try:
            response = await self.client.request(
                method,
                endpoint + path,
                headers=headers,
                json=body,
            )
            if response.status_code >= 400:
                raise ServiceError(
                    "Task execution could not be authorized", status=response.status_code
                )
            receipt = response.json()["data"]
            if (
                not isinstance(receipt, dict)
                or not receipt.get("id")
                or receipt.get("status")
                not in {"pending", "running", "completed", "failed", "cancelled"}
            ):
                raise ValueError("invalid execution receipt")
            return receipt
        except ServiceError:
            raise
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise ServiceError("Task execution service is unavailable", status=503) from exc
