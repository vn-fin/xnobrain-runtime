"""Conversation, chat run, and approval contracts."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ConversationOwnershipContext(BaseModel):
    """Immutable owner and payer supplied by Control after authorization."""

    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    id: str = Field(min_length=1, max_length=256)
    owner_kind: Literal["personal", "organization"]
    organization_id: str | None = Field(default=None, min_length=1, max_length=256)
    owner_label: str | None = Field(default=None, max_length=200)
    payer_kind: Literal["personal", "organization_sponsor"]
    sponsor_grant_id: str | None = Field(default=None, min_length=1, max_length=256)
    membership_revision_at_create: int | None = Field(default=None, ge=1)
    policy_revision_at_create: int | None = Field(default=None, ge=1)
    revocation_version: int | None = Field(default=None, ge=1)
    state: Literal["active", "revoked_read_only", "suspended", "archived"] = "active"

    @model_validator(mode="after")
    def validate_owner_and_payer(self):
        if self.owner_kind == "personal":
            if self.organization_id or self.payer_kind != "personal" or self.sponsor_grant_id:
                raise ValueError("personal ownership requires personal payer and no organization")
        elif not self.organization_id:
            raise ValueError("organization ownership requires organization_id")
        if self.payer_kind == "organization_sponsor" and not self.sponsor_grant_id:
            raise ValueError("organization sponsor payer requires sponsor_grant_id")
        if self.payer_kind == "personal" and self.sponsor_grant_id:
            raise ValueError("personal payer cannot include sponsor_grant_id")
        return self


class ConversationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(default="New Session", max_length=200)
    creation_intent: str | None = Field(default=None, min_length=1, max_length=256)
    ownership_context: ConversationOwnershipContext | None = None


class ConversationRename(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class ConversationCompact(BaseModel):
    focus: str | None = Field(default=None, max_length=500)


class DiagramAttachment(BaseModel):
    """Request-scoped mind map XML. Never persist before send; extra fields forbidden."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["diagram"]
    filename: str = Field(
        min_length=10, max_length=32, pattern=r"^(?:sketch|flowchart|mindmap)(?:-[1-9]\d*)?\.xml$"
    )
    mime_type: Literal["application/xml", "text/xml"]
    content: str = Field(min_length=1, max_length=64_000)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input: str = Field(default="")
    attachment: DiagramAttachment | None = None
    attachments: list[DiagramAttachment] | None = Field(default=None, min_length=1, max_length=8)

    model: str | None = None
    skills: list[str] | None = None
    toolsets: list[str] | None = None
    timeout_seconds: int | None = Field(default=None, gt=0)
    run_mode: Literal["auto", "interactive", "background"] = "auto"
    idempotency_key: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )
    feature: (
        Literal[
            "todo", "delegate", "goal", "learn", "agent_maker", "optimize_skills", "custom_page"
        ]
        | None
    ) = None

    capabilities: list[Literal["todo", "delegate", "goal", "custom_page"]] | None = Field(
        default=None, max_length=4
    )

    @model_validator(mode="after")
    def require_input_or_attachment(self):
        if not self.input.strip() and self.attachment is None and not self.attachments:
            raise ValueError("input or attachment is required")
        return self

    @model_validator(mode="after")
    def validate_capability_selection(self):
        if self.capabilities is None:
            return self
        if len(self.capabilities) != len(set(self.capabilities)):
            raise ValueError("capabilities must be unique")
        if self.feature is not None and self.capabilities != [self.feature]:
            raise ValueError("feature and capabilities must describe the same selection")
        order = ("todo", "delegate", "goal", "custom_page")
        self.capabilities = [value for value in order if value in self.capabilities]
        return self


class GoalContractInput(BaseModel):
    outcome: str = Field(default="", max_length=2_000)
    verification: str = Field(default="", max_length=2_000)
    constraints: str = Field(default="", max_length=2_000)
    boundaries: str = Field(default="", max_length=2_000)
    stop_when: str = Field(default="", max_length=2_000)


class GoalCreate(BaseModel):
    objective: str = Field(min_length=1, max_length=10_000)
    max_turns: int = Field(default=20, ge=1, le=100)
    contract: GoalContractInput = Field(default_factory=GoalContractInput)


class GoalUpdate(GoalCreate):
    pass


class SubgoalCreate(BaseModel):
    text: str = Field(min_length=1, max_length=2_000)


class RunApproval(BaseModel):
    choice: Literal["once", "session", "always", "deny"]
    resolve_all: bool = False
    subsystem: Literal["skills", "memory"] | None = None


class ChildRunCancel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)
    idempotency_key: str = Field(
        min_length=1,
        max_length=256,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )


class TodoRevisionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)
    idempotency_key: str = Field(
        min_length=1,
        max_length=256,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )
    status: Literal["pending", "running", "blocked", "failed", "cancelled", "completed"]
    evidence: str | None = Field(default=None, max_length=10_000)


class ConversationReasoningUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reasoning_effort: (
        Literal["auto", "none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"] | None
    )
    expected_revision: int = Field(ge=0, strict=True)
