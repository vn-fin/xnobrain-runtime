"""Private plugin artifact authority route."""

from ..models.plugin_artifact import (
    PluginArtifactAuthorityRequest,
    PluginArtifactAuthorityResult,
)
from .definition import route

ROUTES = (
    route(
        "POST",
        "/internal/plugin-artifact/authority",
        "plugin_artifact_authority",
        PluginArtifactAuthorityRequest,
        tags=("System",),
        include_in_schema=False,
        response_data=PluginArtifactAuthorityResult,
    ),
)
