"""Closed declarative Agent Custom Page v1 contracts; never executable code."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Identifier = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,47}$")]
Digest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
Key = Annotated[str, Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")]
KINDS = ("table", "list", "card", "chart", "markdown", "details", "form", "action")


def canonical(value: object) -> str:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False
    )


def digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical(value).encode()).hexdigest()


class Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class DataField(Closed):
    id: Identifier
    label: str = Field(min_length=1, max_length=100)
    type: Literal["string", "integer", "number", "boolean", "datetime", "reference", "attachment"]
    required: bool = False
    target: Identifier | None = None

    @model_validator(mode="after")
    def reference_target(self):
        if (self.type == "reference") != (self.target is not None):
            raise ValueError("only reference fields require a target dataset")
        return self


class Dataset(Closed):
    id: Identifier
    label: str = Field(min_length=1, max_length=100)
    fields: list[DataField] = Field(min_length=1, max_length=40)


class Query(Closed):
    id: Identifier
    dataset: Identifier
    operation: Literal["select", "count", "group"] = "select"
    fields: list[Identifier] = Field(default_factory=list, max_length=40)
    filters: list[Identifier] = Field(default_factory=list, max_length=10)
    search: list[Identifier] = Field(default_factory=list, max_length=10)
    sort: Identifier | None = None
    descending: bool = False
    group_by: Identifier | None = None


class PageAction(Closed):
    id: Identifier
    label: str = Field(min_length=1, max_length=100)
    kind: Literal["collect", "analyze"]
    instruction: str = Field(min_length=1, max_length=2000)
    datasets: list[Identifier] = Field(min_length=1, max_length=12)


class Widget(Closed):
    id: Identifier
    kind: Literal["table", "list", "card", "chart", "markdown", "details", "form", "action"]
    title: str = Field(min_length=1, max_length=120)
    query: Identifier | None = None
    action: Identifier | None = None
    text_field: Identifier | None = None
    columns: list[Identifier] = Field(default_factory=list, max_length=40)
    chart: Literal["bar", "line"] = "bar"
    span: Literal[1, 2] = 1


class Tab(Closed):
    id: Identifier
    label: str = Field(min_length=1, max_length=80)
    widgets: list[Widget] = Field(min_length=1, max_length=20)


class PageManifest(Closed):
    schema_version: Literal[1] = 1
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    datasets: list[Dataset] = Field(min_length=1, max_length=12)
    queries: list[Query] = Field(min_length=1, max_length=40)
    actions: list[PageAction] = Field(default_factory=list, max_length=12)
    tabs: list[Tab] = Field(min_length=1, max_length=12)

    @model_validator(mode="after")
    def bindings(self):
        def unique(values):
            ids = [v.id for v in values]
            if len(ids) != len(set(ids)):
                raise ValueError("duplicate manifest identifier")

        for values in (self.datasets, self.queries, self.actions, self.tabs):
            unique(values)
        datasets = {d.id: {f.id: f for f in d.fields} for d in self.datasets}
        queries = {q.id: q for q in self.queries}
        actions = {a.id for a in self.actions}
        if any(tab.id == "activity" for tab in self.tabs):
            raise ValueError("activity is reserved for the app activity view")
        widgets = [w for tab in self.tabs for w in tab.widgets]
        unique(widgets)
        if len(widgets) > 48 or len(canonical(self.model_dump()).encode()) > 262144:
            raise ValueError("manifest exceeds limits")
        for dataset in self.datasets:
            unique(dataset.fields)
            for field in dataset.fields:
                if field.target is not None and field.target not in datasets:
                    raise ValueError("reference target is not declared")
        for query in self.queries:
            fields = datasets.get(query.dataset)
            if fields is None:
                raise ValueError("query dataset is not declared")
            requested = query.fields + query.filters + query.search
            requested += [v for v in (query.sort, query.group_by) if v]
            if any(f not in fields for f in requested):
                raise ValueError("query field is not declared")
            if (query.operation == "group") != bool(query.group_by):
                raise ValueError("group query requires group_by")
            if any(fields[f].type != "string" for f in query.search):
                raise ValueError("search supports string fields only")
        for action in self.actions:
            if not set(action.datasets) <= datasets.keys():
                raise ValueError("action dataset is not declared")
        for widget in widgets:
            if widget.kind == "action":
                if widget.action not in actions or widget.query:
                    raise ValueError("action widget requires a registered action")
                continue
            if widget.query not in queries or widget.action:
                raise ValueError("data widget requires a registered query")
            query = queries[widget.query]
            fields = datasets[query.dataset]
            if not set(widget.columns) <= fields.keys():
                raise ValueError("widget column is not declared")
            if widget.kind == "chart" and query.operation != "group":
                raise ValueError("chart requires grouped results")
            if widget.kind == "card" and query.operation != "count":
                raise ValueError("card requires count results")
            if (
                widget.kind in {"table", "list", "details", "form", "markdown"}
                and query.operation != "select"
            ):
                raise ValueError("record display requires select results")
            if query.fields and not set(widget.columns) <= set(query.fields):
                raise ValueError("widget columns must be projected by the query")
            if widget.kind == "markdown" and (
                widget.text_field not in fields
                or fields[widget.text_field].type != "string"
                or (query.fields and widget.text_field not in query.fields)
            ):
                raise ValueError("markdown requires a projected string field")
        return self


class PageCapabilities(Closed):
    schema_versions: list[int]
    widgets: list[str]
    scope: Literal["personal"]
    executable_components: Literal[False]
    max_records: int
    max_bytes: int
    query_actions_paid: Literal[False]
    scheduled_updates: bool
    operations: list[str]
    max_action_model_turns: int


class PreparePage(Closed):
    expected_revision: int = Field(ge=0)
    idempotency_key: Key
    manifest: PageManifest


class PageApproval(Closed):
    expected_revision: int = Field(ge=0)
    revision: int = Field(ge=1)
    digest: Digest
    confirmation: str = Field(max_length=160)


class QueryPage(Closed):
    parameters: dict[str, str | int | float | bool | None] = Field(
        default_factory=dict, max_length=10
    )
    search: str = Field(default="", max_length=200)
    offset: int = Field(default=0, ge=0, le=10000)
    limit: int = Field(default=50, ge=1, le=100)
    draft_revision: int | None = Field(default=None, ge=1)
    expected_revision: int | None = Field(default=None, ge=1)


class Provenance(Closed):
    kind: Literal["source", "generated", "synthetic"]
    source: str = Field(min_length=1, max_length=1000)
    run_id: str = Field(default="", max_length=128)
    collected_at: str = Field(min_length=1, max_length=40)

    @model_validator(mode="after")
    def generated_run(self):
        if self.kind == "generated" and not self.run_id:
            raise ValueError("generated records require run attribution")
        if re.search(r"https?://[^/@\s]+:[^/@\s]+@", self.source, re.I):
            raise ValueError("credential-bearing source is not allowed")
        return self


class DataRecord(Closed):
    id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    values: dict[str, str | int | float | bool | None] = Field(max_length=40)
    provenance: Provenance


class WriteRecords(Closed):
    expected_revision: int = Field(ge=1)
    idempotency_key: Key
    records: list[DataRecord] = Field(min_length=1, max_length=100)


class PageLifecycle(Closed):
    expected_revision: int = Field(ge=0)
    confirmation: str = Field(max_length=160)


class PageAttachment(Closed):
    idempotency_key: Key
    filename: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9._ -]+$")
    content_base64: str = Field(min_length=1, max_length=1400000)


class MigrationOperation(Closed):
    operation: Literal["drop_dataset", "drop_field"]
    dataset: Identifier
    field: Identifier | None = None

    @model_validator(mode="after")
    def operation_shape(self):
        if (self.operation == "drop_field") != bool(self.field):
            raise ValueError("drop_field requires field; drop_dataset does not")
        return self


class MigratePage(PageApproval):
    operations: list[MigrationOperation] = Field(min_length=1, max_length=40)
    plan_digest: Digest


class PageRevision(Closed):
    revision: int
    manifest: PageManifest
    digest: Digest
    base_revision: int
    status: Literal["preview_ready", "activated", "cancelled"]
    created_at: str


class RevisionSummary(Closed):
    revision: int
    digest: Digest
    base_revision: int
    status: Literal["preview_ready", "activated", "cancelled"]
    created_at: str


class PageState(Closed):
    active: int
    status: Literal["draft", "active", "archived"]
    updated_at: str
    page: PageRevision | None
    revisions: list[RevisionSummary]


class PageRemovalCheck(Closed):
    app: PageState | None


class RetainedPage(Closed):
    agent_id: str
    title: str
    active_revision: int = Field(ge=0)
    updated_at: str


class RetainedPages(Closed):
    items: list[RetainedPage]
    next_offset: int | None


class QueryResult(Closed):
    rows: list[dict]
    next_offset: int | None
    revision: int
    updated_at: str
    archived: bool


class RunPageAction(Closed):
    expected_revision: int = Field(ge=1)
    conversation_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    idempotency_key: Key
    confirmation: str = Field(max_length=160)
    timeout_seconds: int = Field(ge=10, le=300)


class SchedulePage(Closed):
    expected_revision: int = Field(ge=1)
    action_id: Identifier
    conversation_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    schedule: str = Field(min_length=1, max_length=256)
    timezone: str = Field(min_length=1, max_length=128)
    payer_kind: Literal["personal"]
    timeout_seconds: int = Field(ge=10, le=300)
    max_runs: int = Field(ge=1, le=100)

    @model_validator(mode="after")
    def calendar_timezone(self):
        from .automation import CronCreate

        CronCreate.validate_timezone(self.timezone)
        if len(self.schedule.split()) != 5:
            raise ValueError("five-field calendar schedule required")
        return self


class ApprovePageSchedule(SchedulePage):
    idempotency_key: Key
    digest: Digest
    confirmation: str = Field(max_length=160)


class StopPageSchedule(Closed):
    confirmation: str = Field(max_length=160)


class RemovalRecovery(Closed):
    state: Literal["none", "recoverable", "blocked", "complete"]
    operation_id: str | None
    expected_revision: int = Field(ge=0)
    profile_state: Literal["original", "missing", "replaced", "unverified"]
    next_action: Literal["none", "remove_original", "finalize"]
    digest: Digest | None


class RecoverRemoval(Closed):
    operation_id: str = Field(pattern=r"^rem_[a-f0-9]{32}$")
    expected_revision: int = Field(ge=0)
    digest: Digest
    confirmation: str = Field(max_length=160)
