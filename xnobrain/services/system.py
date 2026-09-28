"""System build identity behavior."""

import re

from ..models.system import BuildSHA
from ..repositories.system import read_build_sha


def build_sha() -> BuildSHA:
    """Report a full commit only when embedded by the artifact builder."""
    sha = read_build_sha()
    return BuildSHA(sha=sha if re.fullmatch(r"[0-9a-f]{40}", sha) else "")
