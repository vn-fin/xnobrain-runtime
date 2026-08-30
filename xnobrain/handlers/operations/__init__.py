"""Composable operation registry assembled from feature modules."""

from typing import Any, Callable

from .agents import operations as agents_operations
from .analytics import operations as analytics_operations
from .automation import operations as automation_operations
from .checkpoints import operations as checkpoints_operations
from .conversations import operations as conversations_operations
from .kanban import operations as kanban_operations
from .hosted import operations as hosted_operations
from .marketplace import operations as marketplace_operations
from .organization_artifacts import operations as organization_artifacts_operations
from .mcp import operations as mcp_operations
from .portability import operations as portability_operations
from .providers import operations as providers_operations
from .sandboxes import operations as sandboxes_operations
from .system import operations as system_operations
from .teams import operations as teams_operations
from .workspaces import operations as workspaces_operations

Operation = tuple[Callable[[], Any], str, int]
OPERATION_GROUPS = (
    agents_operations,
    analytics_operations,
    automation_operations,
    checkpoints_operations,
    conversations_operations,
    kanban_operations,
    organization_artifacts_operations,
    marketplace_operations,
    hosted_operations,
    mcp_operations,
    portability_operations,
    providers_operations,
    sandboxes_operations,
    system_operations,
    teams_operations,
    workspaces_operations,
)

def resolve(handler: Any, name: str, request: Any, body: dict[str, Any]) -> Operation:
    for factory in OPERATION_GROUPS:
        operation = factory(handler, request, body).get(name)
        if operation is not None:
            return operation
    raise ValueError("unsupported route")
