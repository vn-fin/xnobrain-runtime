"""Workspaces methods for the Hermes runtime adapter."""

from .hermes_support import (
    AgentAPIError,
    Any,
    MAX_FILE_BYTES,
    MAX_TEXT_CHARS,
    Mapping,
    base64,
    shutil,
)


class WorkspacesMixin:
    def list_workspace(self, raw_name: Any, body: Mapping[str, Any] | None = None) -> dict[str, Any]:
        name = self._agent_name(raw_name)
        self._require_profile(name)
        body = body or {}
        directory = self._workspace_path(name, body.get("path") or ".", require_file=False)
        if not directory.is_dir():
            raise AgentAPIError("path is not a directory", code="invalid_workspace_path")
        entries = []
        for child in sorted(directory.iterdir(), key=lambda item: item.name):
            stat = child.stat()
            entries.append(
                {
                    "name": child.name,
                    "path": str(child.relative_to(self._workspace_dir(name))),
                    "type": "directory" if child.is_dir() else "file",
                    "size_bytes": stat.st_size,
                    "updated_at": stat.st_mtime,
                }
            )
        return {
            "object": "hermes.agent_workspace",
            "agent": name,
            "path": str(directory.relative_to(self._workspace_dir(name))),
            "entries": entries,
        }


    def read_workspace_file(self, raw_name: Any, body: Mapping[str, Any]) -> dict[str, Any]:
        name = self._agent_name(raw_name)
        self._require_profile(name)
        file_path = self._workspace_path(name, body.get("path"), require_file=True)
        if not file_path.is_file():
            raise AgentAPIError("path is not a file", code="invalid_workspace_path")
        size = file_path.stat().st_size
        if size > MAX_FILE_BYTES:
            raise AgentAPIError(
                f"file is too large (max {MAX_FILE_BYTES} bytes)",
                code="workspace_file_too_large",
                status=413,
            )
        content = file_path.read_bytes()
        return {
            "object": "hermes.agent_workspace_file",
            "agent": name,
            "path": str(file_path.relative_to(self._workspace_dir(name))),
            "size_bytes": len(content),
            "content_base64": base64.b64encode(content).decode("ascii"),
        }


    def write_workspace_file(self, raw_name: Any, body: Mapping[str, Any]) -> dict[str, Any]:
        name = self._agent_name(raw_name)
        self._require_profile(name)
        file_path = self._workspace_path(name, body.get("path"), require_file=True)
        if "content_base64" in body:
            try:
                content = base64.b64decode(str(body["content_base64"]), validate=True)
            except Exception as exc:
                raise AgentAPIError("content_base64 is invalid", code="invalid_content") from exc
        else:
            content = self._text_value(body.get("content"), field="content", max_chars=MAX_TEXT_CHARS).encode("utf-8")
        if len(content) > MAX_FILE_BYTES:
            raise AgentAPIError(
                f"content is too large (max {MAX_FILE_BYTES} bytes)",
                code="workspace_file_too_large",
                status=413,
            )
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(content)
        return {
            "object": "hermes.agent_workspace_file",
            "agent": name,
            "path": str(file_path.relative_to(self._workspace_dir(name))),
            "size_bytes": len(content),
        }


    def delete_workspace_path(self, raw_name: Any, body: Mapping[str, Any]) -> dict[str, Any]:
        name = self._agent_name(raw_name)
        self._require_profile(name)
        target = self._workspace_path(name, body.get("path"), require_file=False)
        if target == self._workspace_dir(name):
            raise AgentAPIError("cannot delete workspace root", code="invalid_workspace_path")
        if not target.exists():
            raise AgentAPIError("path not found", code="workspace_path_not_found", status=404)
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        return {"object": "hermes.agent_workspace_delete", "agent": name, "deleted": True}


    def read_memory(self, raw_name: Any) -> dict[str, Any]:
        name = self._agent_name(raw_name)
        profile_dir = self._require_profile(name)
        mem_dir = profile_dir / "memories"
        return {
            "object": "hermes.agent_memory",
            "agent": name,
            "memory": self._read_text(mem_dir / "MEMORY.md"),
            "user": self._read_text(mem_dir / "USER.md"),
        }


    def write_memory(self, raw_name: Any, body: Mapping[str, Any]) -> dict[str, Any]:
        name = self._agent_name(raw_name)
        profile_dir = self._require_profile(name)
        mem_dir = profile_dir / "memories"
        mem_dir.mkdir(parents=True, exist_ok=True)
        if "memory" in body:
            self._write_text(mem_dir / "MEMORY.md", body["memory"])
        if "user" in body:
            self._write_text(mem_dir / "USER.md", body["user"])
        return self.read_memory(name)
