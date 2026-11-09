"""Private source-owner status route."""

from ..models.source_owner import SourceOwnerProbeRequest, SourceOwnerProbeResult
from .definition import route

ROUTES = (
    route(
        "POST",
        "/internal/source-owner/probe",
        "source_owner_probe",
        SourceOwnerProbeRequest,
        tags=("System",),
        include_in_schema=False,
        response_data=SourceOwnerProbeResult,
    ),
)
