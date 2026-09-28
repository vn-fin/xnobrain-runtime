"""Runtime build identity contract."""

from pydantic import BaseModel


class BuildSHA(BaseModel):
    """Empty SHA means the running artifact has no valid build identity."""

    sha: str = ""
