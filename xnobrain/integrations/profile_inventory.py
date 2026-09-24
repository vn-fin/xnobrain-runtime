"""Profile inventory for explicit Runtime roots without upstream global patches."""

from pathlib import Path
from typing import Any, Mapping


def list_profile_inventory(manager: Any, registry: Mapping[str, Any]) -> list[Any]:
    """Use pinned engine metadata readers with Runtime-owned profile discovery.

    The engine's native discovery assumes ``home/profiles``. Runtime supports
    sibling roots and legacy profiles, so identities come from its registry.
    Never use paths supplied by a persisted registry or a browser request.
    """
    from hermes_cli import profiles

    result = []
    for name in registry:
        profile = Path(manager.profile_path(name))
        if profile.is_symlink() or not profile.is_dir():
            continue
        model, provider = profiles._read_config_model(profile)
        meta = profiles.read_profile_meta(profile)
        result.append(
            profiles.ProfileInfo(
                name=name,
                path=profile,
                is_default=name == "default",
                gateway_running=profiles._check_gateway_running(profile),
                model=model,
                provider=provider,
                has_env=(profile / ".env").is_file(),
                skill_count=profiles._count_skills(profile),
                description=meta.get("description", ""),
            )
        )
    return result
