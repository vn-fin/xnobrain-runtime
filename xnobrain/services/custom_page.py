"""Personal agent pages: validated proposals, explicit activation, stored queries."""

from __future__ import annotations

import base64
import math
from datetime import datetime

from pydantic import ValidationError

from ..models.custom_page import (
    KINDS,
    MigratePage,
    PageApproval,
    PageAttachment,
    PageLifecycle,
    PreparePage,
    QueryPage,
    WriteRecords,
)
from ..repositories.custom_page import CustomPageRepository, fail
from .base import ServiceError


def validated(model, body):
    try:
        return model.model_validate(body).model_dump()
    except (ValidationError, ValueError):
        raise ServiceError(
            "custom page input is invalid", status=422, code="custom_page_invalid_input"
        ) from None


class CustomPageService:
    def __init__(self, platform):
        self.platform = platform
        self.repository = CustomPageRepository(platform.repository)

    @staticmethod
    def owner_identity(trusted):
        if not trusted or not trusted.subject:
            fail("trusted_subject_required", 401)
        context = trusted.ownership_context or {}
        if trusted.organization_id or context.get("owner_kind") == "organization":
            fail("custom_page_organization_unsupported", 403)
        if context.get("state", "active") != "active":
            fail("custom_page_authority_revoked", 403)
        return f"{trusted.tenant_id}\x00{trusted.subject}"

    def authority(self, agent_id, trusted, *, retained=False):
        owner = self.owner_identity(trusted)
        # Managed Control resolves the workspace. IDs below only select profiles.
        profile = self.platform.repository.live_profile_path(agent_id)
        if not profile.is_dir():
            if not retained:
                fail("agent_not_found", 404)
            with self.repository.database(agent_id, owner) as db:
                if self.repository.state(db)["status"] != "archived":
                    fail("custom_page_not_found", 404)
        return owner

    def capabilities(self, agent_id, trusted):
        self.authority(agent_id, trusted, retained=True)
        profile_present = self.platform.repository.live_profile_path(agent_id).is_dir()
        operations = ["read", "preview", "query", "activity", "backup", "export", "delete"]
        if profile_present:
            operations += [
                "prepare",
                "activate",
                "restore",
                "write",
                "archive",
                "cancel",
                "migration",
                "action",
            ]
            if agent_id != "big-brother":
                operations += ["remove_agent"]
            if hasattr(self, "schedules"):
                operations += ["schedules"]
        return {
            "schema_versions": [1],
            "widgets": list(KINDS),
            "scope": "personal",
            "executable_components": False,
            "max_records": 10000,
            "max_bytes": 67108864,
            "query_actions_paid": False,
            "scheduled_updates": profile_present and hasattr(self, "schedules"),
            "operations": operations,
            "max_action_model_turns": 20,
        }

    def require_mutation(self):
        updates = getattr(self.platform, "runtime_updates", None)
        if updates is not None:
            updates.require_dispatch()

    def read(self, agent_id, trusted):
        return self.repository.read(agent_id, self.authority(agent_id, trusted, retained=True))

    def prepare(self, agent_id, body, trusted):
        self.require_mutation()
        owner = self.authority(agent_id, trusted)
        return self.repository.prepare(agent_id, owner, validated(PreparePage, body))

    def preview(self, agent_id, revision, trusted):
        return self.repository.preview(
            agent_id, self.authority(agent_id, trusted, retained=True), int(revision)
        )

    @staticmethod
    def compatible(previous, target):
        # Additive migrations change manifest structure in the same transaction as
        # the pointer. No arbitrary DDL and no loss of existing values or records.
        datasets = {d["id"]: d for d in target["datasets"]}
        for dataset in previous["datasets"]:
            if dataset["id"] not in datasets:
                fail("custom_page_destructive_migration_requires_approval")
            fields = {f["id"]: f for f in datasets[dataset["id"]]["fields"]}
            old = {f["id"]: f for f in dataset["fields"]}
            for ident, field in old.items():
                if ident not in fields or any(
                    field[k] != fields[ident][k] for k in ("type", "target", "required")
                ):
                    fail("custom_page_destructive_migration_requires_approval")
            if any(f["required"] for k, f in fields.items() if k not in old):
                fail("custom_page_destructive_migration_requires_approval")

    @staticmethod
    def restore_compatible(current, old):
        current_fields = {d["id"]: {f["id"]: f for f in d["fields"]} for d in current["datasets"]}
        for dataset in old["datasets"]:
            if dataset["id"] not in current_fields:
                fail("custom_page_restore_schema_incompatible")
            for field in dataset["fields"]:
                available = current_fields[dataset["id"]].get(field["id"])
                if (
                    available is None
                    or available["type"] != field["type"]
                    or available["target"] != field["target"]
                ):
                    fail("custom_page_restore_schema_incompatible")

    def activate(self, agent_id, body, trusted, *, restore=False):
        self.require_mutation()
        owner = self.authority(agent_id, trusted)
        body = validated(PageApproval, body)
        action = "RESTORE" if restore else "ACTIVATE"
        if body["confirmation"] != f"{action} {body['digest']}":
            fail("custom_page_confirmation_required", 403)
        return self.repository.activate(
            agent_id,
            owner,
            body,
            self.restore_compatible if restore else self.compatible,
            self.validate_records,
            restore=restore,
        )

    @staticmethod
    def value(field, value):
        if value is None:
            if field["required"]:
                fail("custom_page_required_field", 422)
            return
        kind = field["type"]
        valid = False
        if kind in {"string", "reference", "attachment", "datetime"}:
            valid = isinstance(value, str) and len(value.encode()) <= 8192
            if valid and kind == "datetime":
                try:
                    valid = datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is not None
                except ValueError:
                    valid = False
        elif kind == "integer":
            valid = type(value) is int and abs(value) <= 2**53 - 1
        elif kind == "number":
            valid = type(value) in (int, float) and math.isfinite(value) and abs(value) <= 1e15
        elif kind == "boolean":
            valid = type(value) is bool
        if not valid:
            fail("custom_page_field_type_invalid", 422)

    def validate_records(self, manifest, dataset, records):
        schema = next((d for d in manifest["datasets"] if d["id"] == dataset), None)
        if schema is None:
            fail("custom_page_dataset_not_found", 404)
        fields = {f["id"]: f for f in schema["fields"]}
        if len({r["id"] for r in records}) != len(records):
            fail("custom_page_duplicate_record", 422)
        for row in records:
            if not row["values"].keys() <= fields.keys():
                fail("custom_page_unknown_field", 422)
            for field in fields.values():
                self.value(field, row["values"].get(field["id"]))
            self.value({"type": "datetime", "required": True}, row["provenance"]["collected_at"])

    def write(self, agent_id, dataset, body, trusted):
        self.require_mutation()
        owner = self.authority(agent_id, trusted)
        return self.repository.write_records(
            agent_id, owner, dataset, validated(WriteRecords, body), self.validate_records
        )

    def parameters(self, manifest, query, parameters):
        if not parameters.keys() <= set(query["filters"]):
            fail("custom_page_parameter_not_registered", 422)
        fields = {
            f["id"]: f
            for d in manifest["datasets"]
            if d["id"] == query["dataset"]
            for f in d["fields"]
        }
        for key, value in parameters.items():
            self.value(fields[key], value)

    def query(self, agent_id, query_id, body, trusted):
        owner = self.authority(agent_id, trusted, retained=True)
        return self.repository.query(
            agent_id, owner, query_id, validated(QueryPage, body), self.parameters
        )

    def activity(self, agent_id, trusted):
        return self.repository.activity(agent_id, self.authority(agent_id, trusted, retained=True))

    def archive(self, agent_id, body, trusted):
        self.require_mutation()
        owner = self.authority(agent_id, trusted)
        body = validated(PageLifecycle, body)
        if body["confirmation"] != f"ARCHIVE {agent_id}":
            fail("custom_page_confirmation_required", 403)
        if any(
            run.agent_id == agent_id for run in self.platform.conversation_runs._active.values()
        ):
            fail("custom_page_jobs_active")
        result = self.repository.archive(agent_id, owner, body["expected_revision"])
        schedules = getattr(self, "schedules", None)
        if schedules is not None:
            from ..integrations.custom_page_cron import pause_job

            for binding in schedules.repository.list(agent_id, owner):
                pause_job(self.platform.cron, agent_id, binding["id"])
        return result

    def removal_check(self, agent_id, trusted):
        owner = self.authority(agent_id, trusted)
        with self.repository.lock():
            path = self.repository.directory(agent_id)
            if not path.exists() or not any(path.iterdir()):
                return {"app": None}
            return {"app": self.repository.read(agent_id, owner)}

    def retained(self, trusted, offset=0, limit=50):
        owner = self.owner_identity(trusted)
        if offset < 0 or offset > 1000 or limit < 1 or limit > 100:
            fail("custom_page_invalid_pagination", 422)
        return self.repository.retained(owner, offset, limit)

    def remove_agent(self, agent_id, body, trusted):
        """Explicitly retain an archived app, then remove its writer profile."""
        self.require_mutation()
        owner = self.authority(agent_id, trusted, retained=True)
        from ..defaults import BIG_BROTHER_AGENT_ID

        if agent_id == BIG_BROTHER_AGENT_ID:
            fail("protected_agent", 409)
        body = validated(PageLifecycle, body)
        if body["confirmation"] != f"REMOVE AGENT {agent_id}":
            fail("custom_page_confirmation_required", 403)
        if any(
            run.agent_id == agent_id for run in self.platform.conversation_runs._active.values()
        ):
            fail("custom_page_jobs_active")
        return self.repository.remove_agent(
            agent_id,
            owner,
            body["expected_revision"],
            lambda: self.platform.delete_agent(agent_id, retained_page_owner=owner),
        )

    def removal_recovery(self, agent_id, trusted):
        owner = self.authority(agent_id, trusted, retained=True)
        return self.repository.removal_recovery(agent_id, owner)

    def recover_removal(self, agent_id, body, trusted):
        from ..models.custom_page import RecoverRemoval

        self.require_mutation()
        owner = self.authority(agent_id, trusted, retained=True)
        selected = validated(RecoverRemoval, body)
        if selected["confirmation"] != "RECOVER REMOVAL " + selected["digest"]:
            fail("custom_page_confirmation_required", 403)
        if any(
            run.agent_id == agent_id for run in self.platform.conversation_runs._active.values()
        ):
            fail("custom_page_jobs_active")

        def finalize():
            self.platform.agents.sync_profiles_registry()
            self.platform._cache.invalidate("agents")

        return self.repository.recover_removal(
            agent_id,
            owner,
            selected,
            lambda: self.platform.delete_agent(agent_id, retained_page_owner=owner),
            finalize,
        )

    def cancel(self, agent_id, revision, trusted):
        self.require_mutation()
        return self.repository.cancel(agent_id, self.authority(agent_id, trusted), int(revision))

    def backup(self, agent_id, trusted):
        self.require_mutation()
        return self.repository.backup(agent_id, self.authority(agent_id, trusted, retained=True))

    def attachment(self, agent_id, body, trusted):
        self.require_mutation()
        owner = self.authority(agent_id, trusted)
        body = validated(PageAttachment, body)
        try:
            content = base64.b64decode(body["content_base64"], validate=True)
        except ValueError:
            fail("custom_page_attachment_invalid", 422)
        if len(content) > 1024 * 1024:
            fail("custom_page_attachment_too_large", 413)
        return self.repository.attachment(
            agent_id, owner, body["filename"], content, body["idempotency_key"]
        )

    def read_attachment(self, agent_id, attachment_id, trusted):
        return self.repository.read_attachment(
            agent_id, self.authority(agent_id, trusted, retained=True), attachment_id
        )

    def export(self, agent_id, trusted):
        return self.repository.export(agent_id, self.authority(agent_id, trusted, retained=True))

    def delete(self, agent_id, body, trusted):
        self.require_mutation()
        owner = self.authority(agent_id, trusted, retained=True)
        body = validated(PageLifecycle, body)
        if body["confirmation"] != f"DELETE {agent_id}":
            fail("custom_page_confirmation_required", 403)
        if any(
            run.agent_id == agent_id for run in self.platform.conversation_runs._active.values()
        ):
            fail("custom_page_jobs_active")
        return self.repository.delete(agent_id, owner, body["expected_revision"])

    @staticmethod
    def migration_operations(previous, target):
        datasets = {d["id"]: d for d in target["datasets"]}
        operations = []
        for dataset in previous["datasets"]:
            if dataset["id"] not in datasets:
                operations.append(
                    {"operation": "drop_dataset", "dataset": dataset["id"], "field": None}
                )
                continue
            fields = {f["id"]: f for f in datasets[dataset["id"]]["fields"]}
            old = {f["id"]: f for f in dataset["fields"]}
            for ident, field in old.items():
                if ident not in fields:
                    operations.append(
                        {"operation": "drop_field", "dataset": dataset["id"], "field": ident}
                    )
                elif any(field[k] != fields[ident][k] for k in ("type", "target", "required")):
                    fail("custom_page_migration_type_unsupported", 422)
            if any(f["required"] for k, f in fields.items() if k not in old):
                fail("custom_page_migration_type_unsupported", 422)
        if not operations:
            fail("custom_page_no_destructive_migration", 422)
        return operations

    def migration_plan(self, agent_id, revision, trusted):
        return self.repository.migration_plan(
            agent_id, self.authority(agent_id, trusted), int(revision), self.migration_operations
        )

    def migrate(self, agent_id, body, trusted):
        self.require_mutation()
        owner = self.authority(agent_id, trusted)
        body = validated(MigratePage, body)
        if body["confirmation"] != "MIGRATE " + body["plan_digest"]:
            fail("custom_page_confirmation_required", 403)
        if any(
            run.agent_id == agent_id for run in self.platform.conversation_runs._active.values()
        ):
            fail("custom_page_jobs_active")
        return self.repository.migrate(agent_id, owner, body, self.migration_operations)

    def action_scope(self, agent_id, action_id, body, trusted):
        owner = self.authority(agent_id, trusted)
        page = self.repository.read(agent_id, owner)
        if page["status"] != "active" or page["active"] != body["expected_revision"]:
            fail("custom_page_revision_conflict")
        action = next(
            (a for a in page["page"]["manifest"]["actions"] if a["id"] == action_id), None
        )
        if action is None:
            fail("custom_page_action_not_found", 404)
        conversation_id = body["conversation_id"]
        from .conversation_authority import require_binding

        try:
            require_binding(
                self.platform.repository,
                agent_id,
                conversation_id,
                trusted,
                active=True,
                personal=True,
            )
        except ServiceError:
            fail("custom_page_personal_conversation_required", 403)
        return page, action

    async def run_action(
        self, agent_id, action_id, body, trusted, *, dispatch_guard=None, schedule_id=None
    ):
        """Explicit potentially paid work uses the existing durable run/payer gate."""
        from ..models.custom_page import RunPageAction

        self.require_mutation()
        body = validated(RunPageAction, body)
        if body["confirmation"] != "RUN " + action_id:
            fail("custom_page_confirmation_required", 403)
        page, action = self.action_scope(agent_id, action_id, body, trusted)

        def guard():
            self.require_mutation()
            self.action_scope(agent_id, action_id, body, trusted)
            if dispatch_guard is not None:
                dispatch_guard()

        conversation_id = body["conversation_id"]
        prompt = (
            "Run the explicitly approved custom page action "
            + action["id"]
            + ".\n"
            + action["instruction"]
            + "\nAuthorized destination datasets: "
            + ", ".join(action["datasets"])
            + ". Use custom_page_inspect/query/write. Label generated results with sources; "
            "never fabricate completed collection. This action does not authorize scheduling, "
            "schema migration, activation, broader data access, or deletion."
        )
        return await self.platform.start_conversation_run(
            agent_id,
            conversation_id,
            {
                "input": prompt,
                "idempotency_key": "custom-page:" + body["idempotency_key"],
                "timeout_seconds": body["timeout_seconds"],
                "run_mode": "background",
            },
            trusted,
            custom_page_datasets=action["datasets"],
            custom_page_revision=page["active"],
            dispatch_guard=guard,
            custom_page_schedule=schedule_id,
        )
