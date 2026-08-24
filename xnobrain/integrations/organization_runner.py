"""Organization runner connector.

Connects a personal xnobrain runtime to the control plane so it can execute
organization-delegated commands in the member's own workspace. The runtime does
NOT hold a separate device identity: it authenticates with the organization
member's own auth token, which the browser ("Connect personal agent") forwards
down to the local runtime. Every control-plane call carries that token as a
bearer credential and is scoped by the organization in the path.

Transport (control API prefix, member auth token):
  PUT    {control}/organizations/{org}/runner/agent-registrations
  POST   {control}/organizations/{org}/runner/heartbeat
  POST   {control}/organizations/{org}/runner/commands:poll
  POST   {control}/organizations/{org}/runner/commands/{command_id}/ack
  POST   {control}/organizations/{org}/runner/commands/{command_id}/lease
  POST   {control}/organizations/{org}/runner/commands/{command_id}/result

All responses are wrapped as {"success": bool, "data": ..., "message": str}.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional, Protocol

import httpx

_log = logging.getLogger("xnobrain.organization_runner")

DEFAULT_CONTROL_PREFIX = "/xnobrain/api/control/v1"


class RunnerError(RuntimeError):
    """Raised when the control plane rejects a runner request."""

    def __init__(self, message: str, *, status: int = 0, code: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.code = code or message


def _rfc3339(moment: Optional[datetime] = None) -> str:
    moment = (moment or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return moment.isoformat(timespec="microseconds").replace("+00:00", "Z")


@dataclass
class RunnerCommand:
    command_id: str
    run_id: str
    step_id: str
    registration_id: str
    grant_id: str
    lease_id: str
    lease_token: str
    workspace_id: str
    deadline: str
    attempt: int
    payload_reference: str
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "RunnerCommand":
        return cls(
            command_id=str(data.get("command_id", "")),
            run_id=str(data.get("run_id", "")),
            step_id=str(data.get("step_id", "")),
            registration_id=str(data.get("registration_id", "")),
            grant_id=str(data.get("grant_id", "")),
            lease_id=str(data.get("lease_id", "")),
            lease_token=str(data.get("lease_token", "")),
            workspace_id=str(data.get("workspace_id", "")),
            deadline=str(data.get("deadline", "")),
            attempt=int(data.get("attempt", 0) or 0),
            payload_reference=str(data.get("payload_reference", "")),
            raw=data,
        )


class OrganizationRunnerClient:
    """Synchronous control-plane runner client for one organization member."""

    def __init__(
        self,
        control_base_url: str,
        organization_id: str,
        auth_token: str,
        *,
        control_prefix: str = DEFAULT_CONTROL_PREFIX,
        http_client: Optional[httpx.Client] = None,
        timeout: float = 30.0,
    ) -> None:
        if not control_base_url:
            raise RunnerError("control base url is required")
        if not organization_id:
            raise RunnerError("organization id is required")
        if not auth_token:
            raise RunnerError("auth token is required")
        self._base = control_base_url.rstrip("/") + control_prefix
        self.organization_id = organization_id
        self._token = auth_token
        self._client = http_client or httpx.Client(timeout=timeout)
        self._owns_client = http_client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "OrganizationRunnerClient":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def set_auth_token(self, auth_token: str) -> None:
        """Update the forwarded member auth token (e.g. after the browser refreshes it)."""
        if not auth_token:
            raise RunnerError("auth token is required")
        self._token = auth_token

    # -- operations ---------------------------------------------------------

    def publish_inventory(self, inventory_revision: int, agents: list[dict[str, Any]]) -> dict[str, Any]:
        return self._request("PUT", "/runner/agent-registrations", {"inventory_revision": inventory_revision, "agents": agents})

    def heartbeat(self, *, availability: str = "online", runtime_version: str = "") -> dict[str, Any]:
        return self._request("POST", "/runner/heartbeat", {"availability": availability, "runtime_version": runtime_version})

    def poll_commands(
        self, max_commands: int = 10, *, cursor: str = "", wait_seconds: int = 0
    ) -> tuple[list[RunnerCommand], str]:
        data = self._request(
            "POST", "/runner/commands:poll", {"cursor": cursor, "max_commands": max_commands, "wait_seconds": wait_seconds}
        )
        items = data.get("commands") if isinstance(data, dict) else None
        commands = [RunnerCommand.from_json(item) for item in (items or [])]
        next_cursor = str(data.get("next_cursor", "") or data.get("cursor", "")) if isinstance(data, dict) else ""
        return commands, next_cursor

    def acknowledge(self, command_id: str, *, state: str = "acknowledged", local_sequence: int = 0) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/runner/commands/{command_id}/ack",
            {"state": state, "local_sequence": local_sequence, "occurred_at": _rfc3339()},
        )

    def renew_lease(
        self, command_id: str, lease_id: str, *, extend_seconds: int = 60, progress_state: str = "running"
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/runner/commands/{command_id}/lease",
            {"lease_id": lease_id, "extend_seconds": extend_seconds, "progress_state": progress_state},
        )

    def submit_result(
        self,
        command_id: str,
        *,
        state: str,
        result_digest: str,
        safe_summary: Optional[dict[str, Any]] = None,
        usage: Optional[dict[str, Any]] = None,
        artifacts: Optional[list[dict[str, Any]]] = None,
        error_code: str = "",
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"state": state, "occurred_at": _rfc3339(), "result_digest": result_digest}
        if safe_summary is not None:
            body["safe_summary"] = safe_summary
        if usage is not None:
            body["usage"] = usage
        if artifacts is not None:
            body["artifacts"] = artifacts
        if error_code:
            body["error_code"] = error_code
        return self._request("POST", f"/runner/commands/{command_id}/result", body)

    # -- internals ----------------------------------------------------------

    def _request(self, method: str, suffix: str, body: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._base}/organizations/{self.organization_id}{suffix}"
        headers = {"content-type": "application/json", "authorization": f"Bearer {self._token}"}
        try:
            response = self._client.request(method, url, json=body, headers=headers)
        except httpx.HTTPError as error:  # pragma: no cover - network failure path
            raise RunnerError(f"runner request failed: {error}") from error
        return _unwrap(response)


def _unwrap(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    message = str(payload.get("message", "")) if isinstance(payload, dict) else ""
    if response.status_code >= 400 or (isinstance(payload, dict) and payload.get("success") is False):
        raise RunnerError(
            message or f"runner request failed ({response.status_code})",
            status=response.status_code,
            code=message,
        )
    if isinstance(payload, dict) and "data" in payload:
        data = payload["data"]
        return data if isinstance(data, dict) else {"data": data}
    return payload if isinstance(payload, dict) else {}


def build_client_from_env(
    organization_id: str, auth_token: str, http_client: Optional[httpx.Client] = None
) -> OrganizationRunnerClient:
    """Construct a client from environment configuration plus a request-scoped token.

    XNOBRAIN_BRAIN_CONTROL_BASE_URL / API_CONTROL_BASE_URL / CONTROL_URL
        base URL of the control plane.
    CONTROL_API_PREFIX
        control API prefix (defaults to /xnobrain/api/control/v1).
    """
    base_url = (
        os.getenv("XNOBRAIN_BRAIN_CONTROL_BASE_URL")
        or os.getenv("API_CONTROL_BASE_URL")
        or os.getenv("CONTROL_URL")
        or ""
    )
    if not base_url:
        raise RunnerError("control base url is not configured")
    prefix = os.getenv("CONTROL_API_PREFIX", DEFAULT_CONTROL_PREFIX)
    return OrganizationRunnerClient(
        base_url, organization_id, auth_token, control_prefix=prefix, http_client=http_client
    )


@dataclass
class CommandResult:
    """The bounded, safe outcome of executing one command locally."""

    state: str  # "completed" | "failed"
    safe_summary: Optional[dict[str, Any]] = None
    usage: Optional[dict[str, Any]] = None
    artifacts: Optional[list[dict[str, Any]]] = None
    error_code: str = ""
    result_digest: str = ""

    def digest_for(self, command: "RunnerCommand") -> str:
        """A stable digest so a resent result is recognized as a duplicate."""
        if self.result_digest:
            return self.result_digest
        material = json.dumps(
            {
                "command_id": command.command_id,
                "state": self.state,
                "safe_summary": self.safe_summary,
                "usage": self.usage,
                "error_code": self.error_code,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()


class CommandExecutor(Protocol):
    """Executes one delegated command in the local runtime and returns a result.

    Implementations map ``command.registration_id`` to a local agent, resolve the
    task payload, run it (e.g. via the conversation-run service), and project a
    bounded, safe result. Raising is allowed: the orchestrator reports a failed
    result with a stable error code so the run does not hang.
    """

    def execute(self, command: "RunnerCommand") -> CommandResult: ...


class RunnerOrchestrator:
    """Drives the poll -> acknowledge -> execute -> report loop for one member.

    The transport (``client``) and execution (``executor``) are injected so the
    loop is fully testable without a live control plane or Hermes engine.
    """

    def __init__(
        self,
        client: OrganizationRunnerClient,
        executor: CommandExecutor,
        *,
        max_commands: int = 10,
        poll_wait_seconds: int = 20,
        runtime_version: str = "",
    ) -> None:
        self._client = client
        self._executor = executor
        self._max_commands = max_commands
        self._poll_wait_seconds = poll_wait_seconds
        self._runtime_version = runtime_version

    def process_command(self, command: "RunnerCommand") -> CommandResult:
        """Acknowledge, execute (isolating failures), and report one command."""
        try:
            self._client.acknowledge(command.command_id, state="acknowledged")
        except RunnerError as error:
            _log.warning("ack failed for %s: %s", command.command_id, error)

        try:
            result = self._executor.execute(command)
        except Exception as error:  # noqa: BLE001 - a failed step must still be reported
            _log.exception("command %s execution failed", command.command_id)
            result = CommandResult(state="failed", error_code="execution_error",
                                   safe_summary={"error": str(error)[:500]})

        self._client.submit_result(
            command.command_id,
            state=result.state,
            result_digest=result.digest_for(command),
            safe_summary=result.safe_summary,
            usage=result.usage,
            artifacts=result.artifacts,
            error_code=result.error_code,
        )
        return result

    def run_once(self) -> int:
        """One heartbeat + poll cycle. Returns the number of commands processed."""
        try:
            self._client.heartbeat(availability="online", runtime_version=self._runtime_version)
        except RunnerError as error:
            _log.warning("heartbeat failed: %s", error)
        commands, _ = self._client.poll_commands(self._max_commands, wait_seconds=self._poll_wait_seconds)
        for command in commands:
            self.process_command(command)
        return len(commands)

    def run_forever(self, stop: threading.Event, *, idle_sleep_seconds: float = 2.0) -> None:
        """Loop until ``stop`` is set, backing off briefly when idle or erroring."""
        while not stop.is_set():
            try:
                processed = self.run_once()
            except RunnerError as error:
                _log.warning("runner cycle error: %s", error)
                processed = 0
            if processed == 0:
                stop.wait(idle_sleep_seconds)


class RunnerSupervisor:
    """Manages one background runner connection per organization.

    ``connect`` publishes the current local agent inventory to the control plane
    and starts a daemon thread running the orchestrator; ``disconnect`` stops it.
    This is what a runtime API route ("Connect personal agent") drives after the
    browser forwards the member's organization and auth token.

    The client, executor, and inventory are supplied by factories so the runtime
    can bind its real control-plane URL and Hermes execution, while tests inject
    fakes.
    """

    def __init__(
        self,
        client_factory: Callable[[str, str], OrganizationRunnerClient],
        executor_factory: Callable[[OrganizationRunnerClient], CommandExecutor],
        inventory_provider: Callable[[], list[dict[str, Any]]],
    ) -> None:
        self._client_factory = client_factory
        self._executor_factory = executor_factory
        self._inventory_provider = inventory_provider
        self._lock = threading.Lock()
        self._threads: dict[str, tuple[threading.Thread, threading.Event]] = {}

    def is_connected(self, organization_id: str) -> bool:
        with self._lock:
            entry = self._threads.get(organization_id)
            return bool(entry and entry[0].is_alive())

    def connect(self, organization_id: str, auth_token: str, *, inventory_revision: int = 1) -> None:
        client = self._client_factory(organization_id, auth_token)
        agents = self._inventory_provider()
        client.publish_inventory(inventory_revision, agents)
        executor = self._executor_factory(client)
        orchestrator = RunnerOrchestrator(client, executor)
        stop = threading.Event()
        thread = threading.Thread(
            target=orchestrator.run_forever,
            args=(stop,),
            name=f"runner-{organization_id}",
            daemon=True,
        )
        with self._lock:
            self.disconnect(organization_id)
            self._threads[organization_id] = (thread, stop)
        thread.start()

    def disconnect(self, organization_id: str) -> None:
        entry = self._threads.pop(organization_id, None)
        if entry is None:
            return
        thread, stop = entry
        stop.set()
        if thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=5.0)

    def shutdown(self) -> None:
        for organization_id in list(self._threads.keys()):
            self.disconnect(organization_id)
