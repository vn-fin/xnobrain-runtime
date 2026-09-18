"""Runtime-side FT0013 Time Control adapter orchestration."""

from __future__ import annotations

import asyncio
import hmac
import os
from datetime import datetime, timezone
from typing import Any, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..integrations.time_control_schedules import (
    inventory,
    migrate_schedule,
    preview_migration,
)
from ..models.time_control import (
    TimeAdapterResult,
    TimeAdapterState,
    TimeMigrationPreviewResult,
)
from ..repositories.time_control import TimeControlRepository
from .base import ServiceError

_ADAPTER_VERSION = "xnobrain-time-control-v1"
_POLICY_REVISION = "ft0013-v1"
_TERMINAL_STATES = {"succeeded", "partial", "failed"}


class TimeControlService:
    """Apply process-scoped timezone configuration and exact job migrations."""

    def __init__(self, platform):
        self.platform = platform
        self.repository = TimeControlRepository(platform.repository)
        self._lock = asyncio.Lock()
        self._recover_operations()

    def observe(self, *, time_token: str | None = None) -> dict[str, Any]:
        self._authorize(time_token)
        settings = self._settings()
        now = datetime.now(timezone.utc)
        zone = settings["timezone"]
        state = TimeAdapterState(
            effective_timezone=zone,
            scheduler_timezone=zone,
            sandbox_local_time=now.astimezone(ZoneInfo(zone)).isoformat(),
            utc_time=_utc_iso(now),
            observed_at=now,
            capabilities=[
                {
                    "id": "workspace_timezone",
                    "supported": True,
                    "reason": "",
                },
                {
                    "id": "per_job_timezone",
                    "supported": True,
                    "reason": "",
                },
            ],
            restart_required=False,
            reopen_processes=True,
            observations=[
                {
                    "adapter": _ADAPTER_VERSION,
                    "status": "verified",
                    "timezone": zone,
                    "observed_at": now,
                    "reason": "",
                }
            ],
        )
        return state.model_dump(mode="json")

    @staticmethod
    def authorize_workspace_context(workspace: Any, trusted: Any) -> None:
        """Bind body context to the principal authenticated by the relay."""
        if not isinstance(workspace, Mapping) or not trusted.subject:
            raise ServiceError(
                "Runtime Time Control verified workspace identity is required",
                status=401,
                code="time_control_unauthorized",
            )
        if (
            str(workspace.get("user_id") or "") != trusted.subject
            or str(workspace.get("tenant_id") or "") != trusted.tenant_id
        ):
            raise ServiceError(
                "Runtime Time Control workspace identity does not match",
                status=403,
                code="time_control_workspace_mismatch",
            )

    def default_timezone(self) -> str:
        """Return the verified persisted default for new personal schedules."""
        return str(self._settings()["timezone"])

    def schedules(self, *, time_token: str | None = None) -> list[dict[str, Any]]:
        self._authorize(time_token)
        return inventory(self.platform.cron)

    def preview(
        self,
        request: Mapping[str, Any],
        *,
        time_token: str | None = None,
    ) -> dict[str, Any]:
        self._authorize(time_token)
        target = str(request["target_timezone"])
        cutoff = _aware_utc(request["cutoff"])
        items = [dict(item) for item in request["items"]]
        result = TimeMigrationPreviewResult(
            items=preview_migration(self.platform.cron, items, target, cutoff)
        )
        return result.model_dump(mode="json")

    async def apply(
        self,
        request: Mapping[str, Any],
        *,
        time_token: str | None = None,
    ) -> dict[str, Any]:
        self._authorize(time_token)
        async with self._lock:
            current, replay = self._begin(request, "settings")
            if replay is not None:
                return replay
            now = datetime.now(timezone.utc)
            target = str(request["target_timezone"])
            expected = int(request["expected_revision"])
            if int(current["revision"]) != expected:
                if (
                    int(current["revision"]) == expected + 1
                    and current.get("operation_id") == request["operation_id"]
                    and current.get("plan_hash") == request["plan_hash"]
                    and current.get("timezone") == target
                ):
                    return self._finish(
                        request,
                        "settings",
                        verified=True,
                        partial=False,
                        error_code="",
                        observations=self._observations(
                            target,
                            "verified",
                            "",
                            now,
                        ),
                        effective_timezone=target,
                        scheduler_timezone=target,
                        observed_at=now,
                    )
                return self._finish(
                    request,
                    "settings",
                    verified=False,
                    partial=True,
                    error_code="time_settings_revision_stale",
                    observations=self._observations(
                        current["timezone"],
                        "failed",
                        "The Runtime timezone revision changed before apply.",
                        now,
                    ),
                )
            # Validate against the sandbox tz database BEFORE persisting. The
            # Control plane validates with Go's embedded tzdata, which resolves
            # deprecated aliases (e.g. "Asia/Saigon") that a slim runtime image
            # may lack. Persisting an unloadable zone would fail the readback and
            # leave settings.json poisoned so every later call raises 500.
            try:
                ZoneInfo(target)
            except (ZoneInfoNotFoundError, ValueError):
                return self._finish(
                    request,
                    "settings",
                    verified=False,
                    partial=True,
                    error_code="time_zone_unavailable",
                    observations=self._observations(
                        current["timezone"],
                        "failed",
                        "The requested timezone is not available in the sandbox tz database.",
                        now,
                    ),
                )
            previous = dict(current)
            persisted = {
                "schema_version": 1,
                "timezone": target,
                "revision": expected + 1,
                "operation_id": request["operation_id"],
                "plan_hash": request["plan_hash"],
                "fence": request["fence"],
                "policy_revision": _POLICY_REVISION,
                "last_good": {
                    "timezone": previous["timezone"],
                    "revision": previous["revision"],
                },
                "updated_at": _utc_iso(now),
            }
            self.repository.save_settings(persisted)
            readback = self._settings()
            verified = readback["timezone"] == target and int(readback["revision"]) == expected + 1
            if not verified:
                self.repository.save_settings(previous)
            observed_zone = target if verified else previous["timezone"]
            return self._finish(
                request,
                "settings",
                verified=verified,
                partial=not verified,
                error_code="" if verified else "time_readback_mismatch",
                observations=self._observations(
                    observed_zone,
                    "verified" if verified else "failed",
                    "" if verified else "The persisted timezone readback did not match.",
                    now,
                ),
                effective_timezone=observed_zone,
                scheduler_timezone=observed_zone,
                observed_at=now,
            )

    async def migrate(
        self,
        request: Mapping[str, Any],
        *,
        time_token: str | None = None,
    ) -> dict[str, Any]:
        self._authorize(time_token)
        async with self._lock:
            _, replay = self._begin(request, "migration")
            if replay is not None:
                return replay
            cutoff = _aware_utc(request["cutoff"])
            target = str(request["target_timezone"])
            operation_id = str(request["operation_id"])
            item_results = []
            seen: set[str] = set()
            for item in request["items"]:
                identifier = str(item["id"])
                if identifier in seen or item["new_timezone"] != target:
                    item_results.append(
                        {
                            "id": identifier,
                            "status": "failed",
                            "reason": "The migration item is duplicated or does not match the exact plan target.",
                            "revision": int(item["revision"]),
                            "next_run_at": "",
                        }
                    )
                    continue
                seen.add(identifier)
                result = await asyncio.to_thread(
                    migrate_schedule,
                    self.platform.cron,
                    item,
                    target,
                    cutoff,
                    operation_id,
                )
                item_results.append(result)
                self._checkpoint_items(request, item_results)
            partial = any(item["status"] != "succeeded" for item in item_results)
            now = datetime.now(timezone.utc)
            return self._finish(
                request,
                "migration",
                verified=not partial and len(item_results) == len(request["items"]),
                partial=partial,
                error_code="time_schedule_migration_partial" if partial else "",
                observations=self._observations(
                    self._settings()["timezone"],
                    "verified" if not partial else "failed",
                    "" if not partial else "One or more exact schedules were not migrated.",
                    now,
                ),
                item_results=item_results,
                observed_at=now,
            )

    def _begin(
        self,
        request: Mapping[str, Any],
        kind: str,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        self._validate_mutation(request, kind)
        existing = self.repository.operation(str(request["operation_id"]))
        fingerprint = self._fingerprint(request, kind)
        if existing:
            if existing.get("fingerprint") != fingerprint:
                raise ServiceError(
                    "Time Control operation identity conflicts with a prior request",
                    status=409,
                    code="time_control_operation_conflict",
                )
            if existing.get("state") in _TERMINAL_STATES:
                result = existing.get("result")
                if not isinstance(result, dict):
                    raise ServiceError(
                        "Time Control operation result is invalid",
                        status=500,
                        code="time_control_state_invalid",
                    )
                return self._settings(), result
        self._claim_fence(request, fingerprint)
        self.repository.save_operation(
            {
                "schema_version": 1,
                "operation_id": request["operation_id"],
                "kind": kind,
                "state": "applying" if kind == "settings" else "migrating",
                "fingerprint": fingerprint,
                "fence": request["fence"],
                "plan_hash": request["plan_hash"],
                "target_timezone": request["target_timezone"],
                "expected_revision": request["expected_revision"],
                "cutoff": _json_value(request.get("cutoff")),
                "items": _json_value(request.get("items") or []),
                "item_results": existing.get("item_results", []) if existing else [],
                "updated_at": _utc_iso(datetime.now(timezone.utc)),
            }
        )
        return self._settings(), None

    def _validate_mutation(self, request: Mapping[str, Any], kind: str) -> None:
        if not str(request.get("operation_id") or "").strip():
            raise ServiceError(
                "Time Control operation id is required",
                status=400,
                code="time_control_invalid_request",
            )
        if int(request.get("fence") or 0) < 1:
            raise ServiceError(
                "Time Control maintenance fence is invalid",
                status=409,
                code="time_control_stale_fence",
            )
        if not str(request.get("plan_hash") or "").startswith("sha256:"):
            raise ServiceError(
                "Time Control plan hash is invalid",
                status=400,
                code="time_control_invalid_request",
            )
        workspace = request.get("workspace")
        if not isinstance(workspace, Mapping):
            raise ServiceError(
                "Time Control workspace context is required",
                status=400,
                code="time_control_invalid_request",
            )
        if kind == "migration" and (request.get("cutoff") is None or not request.get("items")):
            raise ServiceError(
                "Time Control migration requires exact items and cutoff",
                status=400,
                code="time_control_invalid_request",
            )

    def _claim_fence(self, request: Mapping[str, Any], fingerprint: str) -> None:
        existing = self.repository.fence()
        fence = int(request["fence"])
        existing_fence = int(existing.get("fence") or 0)
        if fence < existing_fence:
            raise ServiceError(
                "Time Control maintenance fence is stale",
                status=409,
                code="time_control_stale_fence",
            )
        if fence == existing_fence and existing:
            if (
                existing.get("operation_id") != request["operation_id"]
                or existing.get("fingerprint") != fingerprint
            ):
                raise ServiceError(
                    "Time Control maintenance fence belongs to another operation",
                    status=409,
                    code="time_control_stale_fence",
                )
            return
        self.repository.save_fence(
            {
                "schema_version": 1,
                "fence": fence,
                "operation_id": request["operation_id"],
                "fingerprint": fingerprint,
                "updated_at": _utc_iso(datetime.now(timezone.utc)),
            }
        )

    def _checkpoint_items(
        self,
        request: Mapping[str, Any],
        item_results: list[dict[str, Any]],
    ) -> None:
        operation = self.repository.operation(str(request["operation_id"]))
        operation["item_results"] = item_results
        operation["updated_at"] = _utc_iso(datetime.now(timezone.utc))
        self.repository.save_operation(operation)

    def _finish(
        self,
        request: Mapping[str, Any],
        kind: str,
        *,
        verified: bool,
        partial: bool,
        error_code: str,
        observations: list[dict[str, Any]],
        item_results: list[dict[str, Any]] | None = None,
        effective_timezone: str | None = None,
        scheduler_timezone: str | None = None,
        observed_at: datetime | None = None,
    ) -> dict[str, Any]:
        now = observed_at or datetime.now(timezone.utc)
        zone = effective_timezone
        result = TimeAdapterResult(
            verified=verified,
            partial=partial,
            error_code=error_code,
            observations=observations,
            item_results=item_results or [],
            effective_timezone=effective_timezone,
            scheduler_timezone=scheduler_timezone,
            sandbox_local_time=(now.astimezone(ZoneInfo(zone)).isoformat() if zone else ""),
            utc_time=_utc_iso(now),
            observed_at=now,
        ).model_dump(mode="json")
        operation = self.repository.operation(str(request["operation_id"]))
        state = "succeeded"
        if not verified or partial:
            state = "partial"
        if error_code and not partial:
            state = "failed"
        operation.update(
            {
                "kind": kind,
                "state": state,
                "result": result,
                "item_results": result["item_results"],
                "updated_at": _utc_iso(now),
                "completed_at": _utc_iso(now),
            }
        )
        self.repository.save_operation(operation)
        return result

    def _settings(self) -> dict[str, Any]:
        value = self.repository.settings()
        try:
            if (
                value.get("schema_version") != 1
                or type(value.get("revision")) is not int
                or int(value["revision"]) < 0
            ):
                raise ValueError
            ZoneInfo(str(value["timezone"]))
        except (KeyError, TypeError, ValueError, ZoneInfoNotFoundError) as error:
            raise ServiceError(
                "Time Control configuration is invalid",
                status=500,
                code="time_control_state_invalid",
            ) from error
        return value

    def _recover_operations(self) -> None:
        """Validate journals at startup; identical retries resume incomplete work."""
        for operation in self.repository.incomplete_operations():
            if not operation.get("operation_id") or not operation.get("fingerprint"):
                raise ServiceError(
                    "Time Control operation journal is invalid",
                    status=500,
                    code="time_control_state_invalid",
                )

    @staticmethod
    def _fingerprint(request: Mapping[str, Any], kind: str) -> str:
        import hashlib
        import json

        payload = {
            "kind": kind,
            "operation_id": request["operation_id"],
            "fence": request["fence"],
            "plan_hash": request["plan_hash"],
            "target_timezone": request["target_timezone"],
            "expected_revision": request["expected_revision"],
            "cutoff": _json_value(request.get("cutoff")),
            "items": _json_value(request.get("items") or []),
        }
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return "sha256:" + digest

    @staticmethod
    def _observations(
        zone: str,
        status: str,
        reason: str,
        now: datetime,
    ) -> list[dict[str, Any]]:
        return [
            {
                "adapter": _ADAPTER_VERSION,
                "status": status,
                "timezone": zone,
                "observed_at": now,
                "reason": reason,
            }
        ]

    @staticmethod
    def _authorize(supplied: str | None) -> None:
        expected = os.getenv("RUNTIME_INTERNAL_SERVICE_TOKEN", "").strip()
        if not expected or not supplied or not hmac.compare_digest(supplied, expected):
            raise ServiceError(
                "Runtime Time Control service identity is required",
                status=401,
                code="time_control_unauthorized",
            )


def _aware_utc(value: Any) -> datetime:
    parsed = value
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(parsed, datetime) or parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ServiceError(
            "Time Control cutoff must include timezone",
            status=400,
            code="time_control_invalid_request",
        )
    return parsed.astimezone(timezone.utc)


def _utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return _utc_iso(value)
    if isinstance(value, Mapping):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    return value
