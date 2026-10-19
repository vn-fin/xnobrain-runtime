"""Organization runner connection contracts."""

from pydantic import BaseModel, ConfigDict, Field


class ConnectOrganization(BaseModel):
    """A Control-issued one-time token for one organization membership."""

    model_config = ConfigDict(extra="forbid")

    organization_id: str = Field(pattern=r"^org_[A-Za-z0-9]{8,64}$")
    enrollment_token: str = Field(min_length=40, max_length=256)
