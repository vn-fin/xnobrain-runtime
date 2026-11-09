"""Private plugin artifact authority request and safe response contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PluginArtifactStatus = Literal[
    "match",
    "not_owned",
    "not_approved",
    "stale",
    "unsupported",
    "unavailable",
]
PluginArtifactSource = Literal["user", "system", "bundled_platform"]

_DIGEST = r"^(?:sha256:)?[a-fA-F0-9]{64}$"
_PLUGIN = r"^[a-z0-9][a-z0-9._-]{0,127}$"
_PLATFORM = r"^[a-z0-9][a-z0-9._-]{0,63}$"
_AGENT = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"


class PluginArtifactAuthorityRequest(BaseModel):
    """Only the browser-selected labels and digest are accepted."""

    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(pattern=_AGENT)
    plugin: str = Field(pattern=_PLUGIN)
    platform: str = Field(pattern=_PLATFORM)
    artifact_digest: str = Field(pattern=_DIGEST)


class PluginEgressAuth(BaseModel):
    """One server-approved credential placement."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["path", "bearer", "header"]
    header: str | None = None
    prefix: str | None = None


class PluginEgressRule(BaseModel):
    """One exact-host approved egress rule."""

    model_config = ConfigDict(extra="forbid")

    host: str
    methods: list[Literal["GET", "POST"]]
    path_template: str
    endpoints: list[str]
    auth: PluginEgressAuth


class PluginEgressRules(BaseModel):
    """The canonical ``plugin-egress-v1`` declaration."""

    model_config = ConfigDict(extra="forbid")

    version: Literal["plugin-egress-v1"]
    rules: list[PluginEgressRule]


class PluginArtifactAuthorityResult(BaseModel):
    """Safe evidence only; no path, content, secret, or foreign owner."""

    model_config = ConfigDict(extra="forbid")

    status: PluginArtifactStatus
    artifact_digest: str | None = Field(default=None, pattern=r"^sha256:[a-f0-9]{64}$")
    source: PluginArtifactSource | None = None
    rules: PluginEgressRules | None = None
