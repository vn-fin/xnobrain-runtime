"""Prepare a managed container without executing any user-owned Python code."""

from __future__ import annotations

import importlib.util
import os
import pwd
import re
import stat
import tempfile
from pathlib import Path

ENVIRONMENT_SOURCE = Path("/proc/1/environ")
CONFIG_DIRECTORY = Path("/run/xnobrain-runtime-config")
DATA_ANCHOR = Path("/opt/data")
VOLUME_GUARD = Path("/opt/xnobrain/xnobrain/integrations/runtime_data_volume.py")


def read_environment(path: Path = ENVIRONMENT_SOURCE) -> dict[str, str]:
    """Read only the operator contract inherited by PID 1, never profile files."""
    result = {}
    for item in path.read_bytes().split(b"\0"):
        if not item or b"=" not in item:
            continue
        raw_key, raw_value = item.split(b"=", 1)
        key = raw_key.decode("ascii", errors="strict")
        if key not in {"HOME", "PATH", "XDG_CONFIG_HOME"} and not key.startswith(
            ("HERMES_", "RUNTIME_", "FT_ENABLE_")
        ):
            continue
        value = raw_value.decode("utf-8", errors="strict")
        if not re.fullmatch(r"[A-Z_][A-Z0-9_]*", key) or any(
            ord(char) < 32 or ord(char) == 127 for char in value
        ):
            raise ValueError("invalid operator environment")
        result[key] = value
    # These values are a managed-image contract, not user profile preferences.
    result.update(
        HOME="/opt/data/home",
        XDG_CONFIG_HOME="/opt/data/home/.config",
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONNOUSERSITE="1",
    )
    return result


def encode_environment(environment: dict[str, str]) -> str:
    """Encode systemd EnvironmentFile assignments (no shell interpolation)."""
    lines = []
    for key, value in sorted(environment.items()):
        if not re.fullmatch(r"[A-Z_][A-Z0-9_]*", key) or any(
            ord(char) < 32 or ord(char) == 127 for char in value
        ):
            raise ValueError("invalid operator environment")
        value = value.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'{key}="{value}"\n')
    return "".join(lines)


def write_environment(environment: dict[str, str], directory: Path = CONFIG_DIRECTORY) -> None:
    """Publish credentials atomically in a root-owned, non-writable directory."""
    content = encode_environment(environment)
    directory.mkdir(mode=0o700, exist_ok=True)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
        raise ValueError("unsafe runtime configuration directory")
    target = directory / "environment"
    if target.is_symlink():
        raise ValueError("unsafe runtime configuration file")
    with tempfile.NamedTemporaryFile(mode="w", dir=directory, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(content)
            stream.flush()
            os.fchmod(stream.fileno(), 0o600)
            os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def verify_data(environment: dict[str, str]) -> None:
    """Load only the stdlib boot guard from the protected Runtime tree."""
    if environment.get("RUNTIME_DATA_MOUNT_REQUIRED") != "true" or environment.get(
        "RUNTIME_DATA_VOLUME_PATH"
    ) != str(DATA_ANCHOR):
        raise ValueError("managed data volume required")
    specification = importlib.util.spec_from_file_location("volume_guard", VOLUME_GUARD)
    if specification is None or specification.loader is None:
        raise ValueError("volume guard unavailable")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    module.verify_volume(
        DATA_ANCHOR,
        environment.get("RUNTIME_DATA_VOLUME_ID", ""),
        initialize=environment.get("RUNTIME_DATA_VOLUME_INITIALIZE") == "true",
    )


def prepare() -> None:
    if os.geteuid() != 0:
        raise PermissionError("root preparation required")
    environment = read_environment()
    verify_data(environment)
    account = pwd.getpwnam("xnobrain")
    if account.pw_uid == 0 or account.pw_gid == 0:
        raise ValueError("non-root runtime account required")
    # Existing files are never recursively chowned at startup. Migration is an
    # explicit offline operation; the root marker remains readable and root-owned.
    os.chown(DATA_ANCHOR, account.pw_uid, account.pw_gid, follow_symlinks=False)
    write_environment(environment)


if __name__ == "__main__":
    try:
        prepare()
    except (OSError, ValueError, KeyError, UnicodeError):
        raise SystemExit("Managed Runtime preparation failed") from None
