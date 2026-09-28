"""Read immutable build metadata, independently of workspace/profile data."""

from pathlib import Path

SHA_FILE = Path("/etc/xnobrain/sha.txt")


def read_build_sha() -> str:
    """Missing metadata is expected in source development and older images."""
    try:
        with SHA_FILE.open(encoding="ascii") as stream:
            return stream.read(128).strip()
    except (OSError, UnicodeError):
        return ""
