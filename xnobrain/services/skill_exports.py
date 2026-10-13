"""Export one installed skill from the Control-resolved personal workspace."""

import hashlib
import json

from ..models.skill_community import CommunitySkillExportReference
from ..repositories.base import StoreError
from ..repositories.skill_exports import read_skill_tree, validate_skill_path
from .marketplace import MarketplaceService
from .portability import PortabilityService


class SkillExportService:
    def __init__(self, repository, agents):
        self.agents = agents
        self.marketplace = MarketplaceService(repository, agents)

    def export(self, context, reference: str):
        if not context.subject or context.organization_id:
            raise StoreError("Personal workspace required", status=403, code="permission_denied")
        selection = CommunitySkillExportReference.model_validate_json(reference)
        validate_skill_path(selection.skill_path)
        profile = self.marketplace._owned_profile(selection.agent_id)
        # Unmarked local profiles belong to this server-resolved personal workspace.
        # Imported profiles additionally carry recipient ownership, which must match.
        marker = profile / ".community-profile-owner.json"
        if marker.exists() or marker.is_symlink():
            owner = json.loads(
                self.marketplace._read_public_text(
                    profile,
                    marker.relative_to(profile),
                    required=True,
                    max_bytes=8192,
                )
            )
            if owner != PortabilityService.owner_record(context):
                raise StoreError("Profile unavailable", status=403, code="permission_denied")
        inventory = self.agents.list_skills(selection.agent_id)["skills"]
        if not any(item["path"] == selection.skill_path for item in inventory):
            raise StoreError("Skill unavailable", status=404, code="not_found")
        content, excluded = read_skill_tree(profile, selection.skill_path)
        config = self.marketplace._read_yaml_object(profile / "config.yaml", required=False)
        credential_values = self.marketplace._credential_values(profile, config)
        files = []
        for path, payload in content.items():
            try:
                text = payload.decode("utf-8")
            except UnicodeDecodeError as error:
                raise StoreError(
                    "Skill export requires UTF-8 text files",
                    status=422,
                    code="skill_export_text_only",
                ) from error
            self.marketplace._reject_secret_content(text, credential_values, path)
            files.append(
                {
                    "path": path,
                    "content": text,
                    "encoding": "utf-8",
                    "size": len(payload),
                    "digest": "sha256:" + hashlib.sha256(payload).hexdigest(),
                }
            )
        manifest = [{key: file[key] for key in ("path", "size", "digest")} for file in files]
        digest = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
        return {
            "files": files,
            "excluded": excluded,
            "provenance": {"source_type": "runtime_export", "source_digest": "sha256:" + digest},
        }
