"""Narrow Control-mediated, non-executable layout assistance contract."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class AssistLayout(Closed):
    schema_version: Literal[1]
    assistance_id: str = Field(pattern=r"^uia_[a-zA-Z0-9-]{1,80}$")
    layout_id: str = Field(pattern=r"^uil_[a-zA-Z0-9-]{1,80}$")
    base_revision: int = Field(ge=1)
    context: str = Field(min_length=1, max_length=128)
    agent_id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$")
    conversation_id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$")
    request: str = Field(min_length=1, max_length=2000)
    timeout_seconds: int = Field(ge=10, le=300)
    catalog: list[dict] = Field(min_length=1, max_length=20)
    layout: dict


class ProposeLayout(Closed):
    layout: dict = Field(
        description=(
            "Closed declarative layout: schema_version=1, name, preset=stacked|two-column, "
            "widgets=[{id,kind,slot}] from the bound catalog. Optional theme=auto|light|dark, "
            "accent=default|teal|blue|violet, density=comfortable|compact, font_scale=0.9..1.25. "
            "Optional inspector_position=right|left|bottom and inspector_height=160..480 (integer pixels). "
            "Optional default_page is a navigation_options ID from ui_layout_catalog. "
            "Optional navigation={order:[IDs],hidden:[IDs]} reorders or folds navigation. "
            "Home/settings cannot be hidden. Omit fields for defaults. No CSS, code or authority fields."
        )
    )
