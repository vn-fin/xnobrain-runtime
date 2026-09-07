"""Compatibility facade for grouped global profile integrations."""

from __future__ import annotations

from .config_support import *  # noqa: F401,F403
from .config_support import DEFAULT_ROOT_PROFILE, ConfigAPIError, Path, os  # noqa: F401
from .global_config import GlobalConfigMixin
from .skills import CommonSkillsMixin


class GlobalConfigManager(CommonSkillsMixin, GlobalConfigMixin):
    def __init__(self, *, root_profile: str | Path | None = None):
        self.root_profile = Path(
            root_profile or os.environ.get("HERMES_ROOT_PROFILE") or DEFAULT_ROOT_PROFILE
        )
