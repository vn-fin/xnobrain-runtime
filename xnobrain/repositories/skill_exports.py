"""Bounded, read-only skill trees; descriptors never follow links."""

import os
import stat
from contextlib import ExitStack
from pathlib import PurePosixPath

from .base import StoreError

MAX_FILES = 200
MAX_FILE_BYTES = 1024 * 1024
MAX_TOTAL_BYTES = 10 * 1024 * 1024
PRIVATE_PARTS = frozenset(
    {
        "credentials",
        "sessions",
        "conversations",
        "conversation-runs",
        "memory",
        "memories",
        "history",
        "logs",
        "cache",
        "caches",
        "__pycache__",
        "node_modules",
        "state.db",
        "auth.json",
        "config.yaml",
        "soul.md",
        "user.md",
        "memory.md",
        "agents.md",
    }
)


def validate_skill_path(value: str) -> tuple[str, ...]:
    path = PurePosixPath(value)
    if (
        not path.parts
        or path.is_absolute()
        or str(path) != value
        or len(path.parts) > 12
        or len(value) > 512
        or any(char in value for char in "\\\x00\r\n:")
        or any(part in {".", ".."} for part in path.parts)
    ):
        raise StoreError("Invalid skill path", status=422, code="invalid_input")
    return path.parts


def private_path(parts: tuple[str, ...]) -> bool:
    return any(part.startswith(".") or part.casefold() in PRIVATE_PARTS for part in parts)


def read_skill_tree(profile, skill_path: str) -> tuple[dict[str, bytes], list[str]]:
    """Read only the selected directory, with bounded traversal and byte counts."""
    parts = validate_skill_path(skill_path)
    if private_path(parts):
        raise StoreError("Skill unavailable", status=404, code="not_found")
    files: dict[str, bytes] = {}
    excluded: list[str] = []
    seen: set[str] = set()
    total = 0
    visited = 0

    def walk(directory: int, prefix: tuple[str, ...] = ()) -> None:
        nonlocal total, visited
        with os.scandir(directory) as entries:
            for entry in entries:
                visited += 1
                if visited > 1000:
                    raise StoreError("Skill too large", status=413, code="limit_exceeded")
                relative = (*prefix, entry.name)
                name = "/".join(relative)
                validate_skill_path(name)
                if private_path(relative):
                    excluded.append(name)
                    continue
                details = entry.stat(follow_symlinks=False)
                if stat.S_ISDIR(details.st_mode):
                    child = os.open(
                        entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory
                    )
                    try:
                        walk(child, relative)
                    finally:
                        os.close(child)
                    continue
                if not stat.S_ISREG(details.st_mode) or details.st_nlink != 1:
                    raise StoreError(
                        "Unsafe skill file", status=422, code="skill_export_unsafe_file"
                    )
                if name.casefold() in seen or (entry.name == "SKILL.md" and prefix):
                    raise StoreError(
                        "Ambiguous skill tree", status=422, code="skill_export_ambiguous_tree"
                    )
                seen.add(name.casefold())
                if len(files) >= MAX_FILES:
                    raise StoreError("Too many skill files", status=413, code="limit_exceeded")
                fd = os.open(
                    entry.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory
                )
                with os.fdopen(fd, "rb") as stream:
                    current = os.fstat(stream.fileno())
                    if not stat.S_ISREG(current.st_mode) or current.st_nlink != 1:
                        raise StoreError(
                            "Unsafe skill file", status=422, code="skill_export_unsafe_file"
                        )
                    content = stream.read(MAX_FILE_BYTES + 1)
                total += len(content)
                if len(content) > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
                    raise StoreError("Skill too large", status=413, code="limit_exceeded")
                files[name] = content

    with ExitStack() as stack:
        directory = os.open(profile, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        stack.callback(os.close, directory)
        for part in ("skills", *parts):
            directory = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory
            )
            stack.callback(os.close, directory)
        walk(directory)
    if "SKILL.md" not in files:
        raise StoreError("SKILL.md required", status=422, code="skill_export_missing_manifest")
    return dict(sorted(files.items())), sorted(excluded)
