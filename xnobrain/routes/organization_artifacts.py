from ..models.organization_artifacts import LocalArtifactInspect, PublishOrganizationArtifact, ImportOrganizationArtifact
from .definition import route
ROUTES=(
 route("POST","/agents-workspaces/{agent_id}/organization-artifacts/inspect","organization_artifact_inspect",LocalArtifactInspect,tags=("Organization artifacts",)),
 route("POST","/agents-workspaces/{agent_id}/organization-artifacts/publish","organization_artifact_publish",PublishOrganizationArtifact,tags=("Organization artifacts",)),
 route("POST","/agents-workspaces/{agent_id}/organization-artifacts/import","organization_artifact_import",ImportOrganizationArtifact,tags=("Organization artifacts",)),
)
