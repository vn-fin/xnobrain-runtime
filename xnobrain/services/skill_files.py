"""Read-only browsing of the selected agent's installed skill files."""

from pathlib import Path

from ..integrations import AgentAPIError


def skill_path(agents, agent_id: str, raw: str, *, file: bool = False) -> Path:
    profile = agents.profile_path(agent_id)
    root = profile / "skills"
    raw = str(raw or "").strip()
    relative = Path(raw)
    if (
        relative.is_absolute()
        or any(p == ".." for p in relative.parts)
        or "\\" in raw
        or "%" in raw
    ):
        raise AgentAPIError("Invalid skill path", code="invalid_skill_path", status=400)
    target = root / relative
    # No symlinks, including links back into another allowed directory.
    for node in (target, *target.parents):
        if node == profile:
            break
        if node.is_symlink():
            raise AgentAPIError("Invalid skill path", code="invalid_skill_path", status=403)
    if not target.resolve().is_relative_to(root.resolve()):
        raise AgentAPIError("Invalid skill path", code="invalid_skill_path", status=403)
    if file and not target.is_file():
        raise AgentAPIError("Skill file not found", code="skill_file_not_found", status=404)
    return target


def list_skill_files(agents, agent_id: str, raw: str) -> dict:
    directory = skill_path(agents, agent_id, raw)
    root = agents.profile_path(agent_id) / "skills"
    if directory == root and not root.exists():
        return {"entries": [], "path": ""}
    if not directory.is_dir():
        raise AgentAPIError("Skill directory not found", code="skill_file_not_found", status=404)
    entries = []
    for child in sorted(directory.iterdir()):
        if child.is_symlink() or not (child.is_file() or child.is_dir()):
            continue
        stat = child.stat()
        entries.append(
            {
                "name": child.name,
                "path": child.relative_to(root).as_posix(),
                "type": "directory" if child.is_dir() else "file",
                "size_bytes": stat.st_size,
                "updated_at": stat.st_mtime,
            }
        )
    return {"entries": entries, "path": directory.relative_to(root).as_posix()}
