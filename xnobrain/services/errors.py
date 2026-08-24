"""Expected application-layer exceptions handled as API responses."""

from ..integrations import AgentAPIError, ConfigAPIError, LLMRouterAPIError
from ..integrations.checkpoints import CheckpointIntegrationError
from ..repositories import StoreError
from .base import ServiceError
from .cron import CronServiceError
from .workspace_preview import WorkspacePreviewError
from .workspace_upload import WorkspaceUploadError

EXPECTED_ERRORS = (
    ServiceError,
    CronServiceError,
    StoreError,
    AgentAPIError,
    ConfigAPIError,
    LLMRouterAPIError,
    WorkspacePreviewError,
    WorkspaceUploadError,
    CheckpointIntegrationError,
)
