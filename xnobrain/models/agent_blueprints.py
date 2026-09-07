"""Typed contracts for the Agent Maker blueprint draft lifecycle."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_SHA256 = r"^sha256:[0-9a-f]{64}$"
_SAFE_REFERENCE = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$"
_SAFE_COMPONENT = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"


class BlueprintModelSlot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    alias: str = Field(min_length=1, max_length=256, pattern=_SAFE_REFERENCE)
    reasoning_effort: Literal["none", "low", "medium", "high"] = "medium"


class BlueprintPersona(BaseModel):
    model_config = ConfigDict(extra="forbid")
    soul: str = Field(min_length=1, max_length=200_000)
    agents_instructions: str = Field(min_length=1, max_length=200_000)


class BlueprintMemorySeed(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=128, pattern=_SAFE_COMPONENT)
    content: str = Field(min_length=1, max_length=100_000)
    provenance: str = Field(min_length=1, max_length=2_000)


class BlueprintMemory(BaseModel):
    model_config = ConfigDict(extra="forbid")
    policy: Literal["context_isolated", "disabled"]
    seed_sources: list[BlueprintMemorySeed] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def disabled_memory_has_no_seed(self):
        if self.policy == "disabled" and self.seed_sources:
            raise ValueError("disabled memory cannot include seed sources")
        return self


class BlueprintSkill(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=128, pattern=_SAFE_COMPONENT)
    digest: str = Field(pattern=_SHA256)
    source: str = Field(min_length=1, max_length=2_000)
    content: str = Field(min_length=1, max_length=500_000)

    @model_validator(mode="after")
    def digest_matches_content(self):
        import hashlib

        actual = "sha256:" + hashlib.sha256(self.content.encode("utf-8")).hexdigest()
        if self.digest != actual:
            raise ValueError("skill digest does not match content")
        return self


class BlueprintWorkspace(BaseModel):
    model_config = ConfigDict(extra="forbid")
    directories: list[str] = Field(default_factory=list, max_length=32)

    @field_validator("directories")
    @classmethod
    def validate_directories(cls, values: list[str]) -> list[str]:
        result = []
        seen = set()
        for value in values:
            if not value or value.startswith(("/", "\\")):
                raise ValueError("workspace directories must be relative")
            parts = value.replace("\\", "/").split("/")
            if any(not part or part in {".", ".."} for part in parts):
                raise ValueError("workspace directory is invalid")
            normalized = "/".join(parts)
            if normalized in seen:
                raise ValueError("workspace directories must be unique")
            seen.add(normalized)
            result.append(normalized)
        return result


class BlueprintTools(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requested: list[str] = Field(default_factory=list, max_length=64)
    mcp_servers: list[str] = Field(default_factory=list, max_length=32)

    @field_validator("requested", "mcp_servers")
    @classmethod
    def validate_references(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("permission references must be unique")
        for value in values:
            if not value or len(value) > 256:
                raise ValueError("permission reference is invalid")
            if any(character in value for character in ("/", "\\", "\x00", "\r", "\n")):
                raise ValueError("permission reference is invalid")
        return values


class BlueprintAutomation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cron_enabled: Literal[False] = False


class BlueprintBudget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    currency: Literal["USD"] = "USD"
    expected_cost: float = Field(default=0, ge=0, le=100_000)


class AgentBlueprintSpec(BaseModel):
    """A complete specialist definition without arbitrary config passthrough."""

    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    name: str = Field(min_length=1, max_length=200)
    purpose: str = Field(min_length=1, max_length=4_000)
    ownership: Literal["personal", "organization"]
    persona: BlueprintPersona
    model_slot: BlueprintModelSlot
    memory: BlueprintMemory
    skills: list[BlueprintSkill] = Field(default_factory=list, max_length=64)
    workspace: BlueprintWorkspace
    tools: BlueprintTools
    automation: BlueprintAutomation = Field(default_factory=BlueprintAutomation)
    budget: BlueprintBudget = Field(default_factory=BlueprintBudget)
    acceptance: list[str] = Field(min_length=1, max_length=64)

    @field_validator("skills")
    @classmethod
    def unique_skills(cls, values: list[BlueprintSkill]) -> list[BlueprintSkill]:
        identifiers = [item.id for item in values]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("skills must have unique ids")
        return values

    @field_validator("acceptance")
    @classmethod
    def validate_acceptance(cls, values: list[str]) -> list[str]:
        if any(not value.strip() or len(value) > 4_000 for value in values):
            raise ValueError("acceptance cases must be non-empty")
        return values


class AgentBlueprintCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    intent: str = Field(min_length=1, max_length=20_000)
    work_context_id: str = Field(min_length=1, max_length=256, pattern=_SAFE_REFERENCE)
    blueprint: AgentBlueprintSpec | None = None


class AgentBlueprintPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    intent: str | None = Field(default=None, min_length=1, max_length=20_000)
    work_context_id: str | None = Field(default=None, pattern=_SAFE_REFERENCE)
    blueprint: AgentBlueprintSpec | None = None

    @model_validator(mode="after")
    def includes_change(self):
        if not self.model_fields_set - {"expected_revision"}:
            raise ValueError("at least one blueprint change is required")
        return self


class AgentBlueprintApprovalCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    canonical_digest: str = Field(pattern=_SHA256)
    decision: Literal["approve"]


class BlueprintFileManifestEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=512)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(ge=0, le=10_000_000)


class BlueprintApprovalBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content_digest: str = Field(pattern=_SHA256)
    permissions_digest: str = Field(pattern=_SHA256)
    model_digest: str = Field(pattern=_SHA256)
    context_digest: str = Field(pattern=_SHA256)


class BlueprintApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approved_revision: int = Field(ge=1)
    canonical_digest: str = Field(pattern=_SHA256)
    binding: BlueprintApprovalBinding
    approved_by: str = Field(min_length=1, max_length=256, pattern=_SAFE_REFERENCE)
    approved_at: str


class AgentBlueprintRecord(BaseModel):
    """Validated representation returned by every blueprint lifecycle endpoint."""

    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^abp_[0-9a-f]{32}$")
    revision: int = Field(ge=1)
    owner_agent_id: str = Field(min_length=1, max_length=128)
    work_context_id: str = Field(pattern=_SAFE_REFERENCE)
    target_profile_id: str = Field(pattern=r"^agent-[0-9a-f]{12}$")
    intent: str = Field(min_length=1, max_length=20_000)
    blueprint: AgentBlueprintSpec | None
    status: Literal["requested", "blueprint_ready", "approved"]
    approval: BlueprintApproval | None
    created_at: str
    updated_at: str
    file_manifest: list[BlueprintFileManifestEntry] = Field(max_length=256)
    approval_binding: BlueprintApprovalBinding
    canonical_digest: str = Field(pattern=_SHA256)
