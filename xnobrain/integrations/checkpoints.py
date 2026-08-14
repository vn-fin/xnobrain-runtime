"""Narrow adapter around Hermes' checkpoint manager."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import os
import re
import shutil
from typing import Any


FULL_HASH = re.compile(r"^[0-9a-f]{40}$")
MAX_DIFF_FILES = 200
MAX_DIFF_LINES = 5_000
MAX_DIFF_BYTES = 512 * 1024


class CheckpointIntegrationError(RuntimeError):
    def __init__(self, message: str, *, code: str = "checkpoint_error", status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


class CheckpointIntegration:
    """Adapt the installed Hermes implementation without exposing its paths."""

    def __init__(self, *, max_snapshots: int = 20, max_total_size_mb: int = 500, max_file_size_mb: int = 10):
        self.max_snapshots = max_snapshots
        self.max_total_size_mb = max_total_size_mb
        self.max_file_size_mb = max_file_size_mb

    def _runtime(self):
        try:
            import tools.checkpoint_manager as checkpoint_module
        except Exception as exc:  # pragma: no cover - depends on runtime packaging
            raise CheckpointIntegrationError(
                "File restore points are unavailable in this runtime",
                code="checkpoints_unavailable",
                status=503,
            ) from exc
        # Hermes resolves this module constant at import time. Tests and the
        # embedded server can select a profile root later, so refresh it here.
        checkpoint_module.CHECKPOINT_BASE = Path(
            os.environ.get("HERMES_HOME", str(Path.home() / ".hermes"))
        ) / "checkpoints"
        manager = checkpoint_module.CheckpointManager(
            enabled=True,
            max_snapshots=self.max_snapshots,
            max_total_size_mb=self.max_total_size_mb,
            max_file_size_mb=self.max_file_size_mb,
        )
        return (
            manager,
            checkpoint_module.CHECKPOINT_BASE,
            checkpoint_module._store_path,
            checkpoint_module._project_hash,
            checkpoint_module._ref_name,
            checkpoint_module._index_path,
            checkpoint_module._run_git,
        )

    def capability(self) -> tuple[bool, str | None]:
        if shutil.which("git") is None:
            return False, "Git is not installed in this runtime"
        try:
            self._runtime()
        except CheckpointIntegrationError as exc:
            return False, str(exc)
        return True, None

    def _context(self, workspace: Path):
        manager, base, store_path, project_hash, ref_name, index_path, run_git = self._runtime()
        root = str(workspace.resolve())
        store = store_path(base)
        ref = ref_name(project_hash(root))
        index = index_path(store, project_hash(root))
        return manager, root, store, ref, index, run_git

    @staticmethod
    def _require_hash(value: str) -> str:
        value = str(value or "").strip().lower()
        if not FULL_HASH.fullmatch(value):
            raise CheckpointIntegrationError("checkpoint id is invalid", code="invalid_checkpoint_id")
        return value

    def _owned_hash(self, workspace: Path, checkpoint_id: str):
        checkpoint_id = self._require_hash(checkpoint_id)
        manager, root, store, ref, index, run_git = self._context(workspace)
        ok, stdout, _ = run_git(
            ["merge-base", "--is-ancestor", checkpoint_id, ref], store, root,
            allowed_returncodes={1, 128, 129},
        )
        if not ok:
            raise CheckpointIntegrationError(
                "restore point was not found for this workspace",
                code="checkpoint_not_found",
                status=404,
            )
        return checkpoint_id, manager, root, store, ref, index, run_git

    def status(self, workspace: Path) -> dict[str, Any]:
        available, reason = self.capability()
        count = len(self.list(workspace)) if available else 0
        return {
            "available": available,
            "unavailable_reason": reason,
            "checkpoint_count": count,
            "retained_bytes": None,
            "max_snapshots": self.max_snapshots,
            "max_file_size_bytes": self.max_file_size_mb * 1024 * 1024,
        }

    def list(self, workspace: Path) -> list[dict[str, Any]]:
        manager, root, *_ = self._context(workspace)
        return [
            {
                "id": item.get("hash", ""),
                "short_id": item.get("short_hash", ""),
                "created_at": item.get("timestamp", ""),
                "reason": item.get("reason", "restore point"),
                "trigger": self._trigger(item.get("reason")),
                "files_changed": int(item.get("files_changed") or 0),
                "insertions": int(item.get("insertions") or 0),
                "deletions": int(item.get("deletions") or 0),
            }
            for item in manager.list_checkpoints(root)
        ]

    @staticmethod
    def _trigger(reason: Any) -> str:
        text = str(reason or "").lower()
        for value in ("patch", "write_file", "terminal", "workspace write", "workspace delete", "workspace rename", "workspace upload", "workspace create", "restore"):
            if value in text:
                return value.replace("workspace ", "")
        return "other"

    def take(self, workspace: Path, reason: str) -> bool:
        manager, root, *_ = self._context(workspace)
        return bool(manager._take(root, str(reason)[:240]))

    def diff(self, workspace: Path, checkpoint_id: str) -> dict[str, Any]:
        checkpoint_id, manager, root, *_ = self._owned_hash(workspace, checkpoint_id)
        result = manager.diff(root, checkpoint_id)
        if not result.get("success"):
            raise CheckpointIntegrationError("could not generate restore point diff", code="checkpoint_diff_failed", status=503)
        raw = str(result.get("diff") or "")
        total_bytes = len(raw.encode("utf-8"))
        lines = raw.splitlines(keepends=True)
        truncated = total_bytes > MAX_DIFF_BYTES or len(lines) > MAX_DIFF_LINES
        patch = "".join(lines[:MAX_DIFF_LINES])
        while len(patch.encode("utf-8")) > MAX_DIFF_BYTES:
            patch = patch[: max(0, len(patch) - 4096)]
        files = self._diff_files(raw)[:MAX_DIFF_FILES]
        truncated = truncated or len(self._diff_files(raw)) > MAX_DIFF_FILES
        return {
            "checkpoint_id": checkpoint_id,
            "short_id": checkpoint_id[:7],
            "files": files,
            "patch": patch,
            "truncated": truncated,
            "total_patch_bytes": total_bytes,
        }

    @staticmethod
    def _diff_files(patch: str) -> list[dict[str, Any]]:
        files: list[dict[str, Any]] = []
        current: dict[str, Any] | None = None
        for line in patch.splitlines():
            if line.startswith("diff --git a/"):
                path = line.split(" b/", 1)[-1]
                current = {"path": path, "status": "modified", "insertions": 0, "deletions": 0, "binary": False}
                files.append(current)
            elif current is not None and line.startswith("new file mode"):
                current["status"] = "added"
            elif current is not None and line.startswith("deleted file mode"):
                current["status"] = "deleted"
            elif current is not None and line.startswith("Binary files"):
                current["binary"] = True
            elif current is not None and line.startswith("+") and not line.startswith("+++"):
                current["insertions"] += 1
            elif current is not None and line.startswith("-") and not line.startswith("---"):
                current["deletions"] += 1
        return files

    def file_versions(self, workspace: Path, relative: str) -> dict[str, Any]:
        manager, root, store, ref, index, run_git = self._context(workspace)
        current_path = workspace / relative
        current = {"exists": current_path.is_file(), "size": None, "modified_at": None}
        if current_path.is_file():
            stat = current_path.stat()
            current.update(size=stat.st_size, modified_at=datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat().replace("+00:00", "Z"))
        items: list[dict[str, Any]] = []
        previous: tuple[bool, str | None] | None = None
        for checkpoint in manager.list_checkpoints(root):
            commit = str(checkpoint.get("hash") or "")
            ok, output, _ = run_git(["ls-tree", commit, "--", relative], store, root, allowed_returncodes={128, 129})
            exists = bool(ok and output.strip())
            blob_id = None
            size = None
            if exists:
                fields = output.split(None, 3)
                blob_id = fields[2] if len(fields) >= 3 else None
                if blob_id:
                    size_ok, size_out, _ = run_git(["cat-file", "-s", blob_id], store, root)
                    size = int(size_out) if size_ok and size_out.isdigit() else None
            signature = (exists, blob_id)
            if signature == previous:
                continue
            previous = signature
            items.append({
                "checkpoint_id": commit,
                "short_id": str(checkpoint.get("short_hash") or commit[:7]),
                "created_at": checkpoint.get("timestamp", ""),
                "reason": checkpoint.get("reason", "restore point"),
                "exists": exists,
                "blob_id": blob_id,
                "size": size,
            })
        return {"path": relative, "current": current, "items": items}

    def restore(self, workspace: Path, checkpoint_id: str, relative: str | None = None) -> dict[str, Any]:
        checkpoint_id, manager, root, store, ref, index, run_git = self._owned_hash(workspace, checkpoint_id)
        if not manager._take(root, f"before explicit restore to {checkpoint_id[:8]}"):
            raise CheckpointIntegrationError(
                "Current workspace state could not be protected; restore was cancelled",
                code="safety_checkpoint_failed",
                status=503,
            )
        if relative:
            targets = [relative]
        else:
            current_diff = manager.diff(root, checkpoint_id)
            if not current_diff.get("success"):
                raise CheckpointIntegrationError("could not calculate restore impact", code="checkpoint_restore_failed", status=503)
            targets = [item["path"] for item in self._diff_files(str(current_diff.get("diff") or ""))]
        for target in targets:
            ok, output, _ = run_git(["ls-tree", checkpoint_id, "--", target], store, root, allowed_returncodes={128, 129})
            destination = workspace / str(target)
            if not ok or not output.strip():
                if destination.is_dir() and not destination.is_symlink():
                    shutil.rmtree(destination)
                elif destination.exists() or destination.is_symlink():
                    destination.unlink()
                continue
            restored, _, _ = run_git(
                ["checkout", checkpoint_id, "--", str(target)], store, root,
                index_file=index, allowed_returncodes={128, 129},
            )
            if not restored:
                raise CheckpointIntegrationError("restore failed", code="checkpoint_restore_failed", status=503)
        return {"safety_checkpoint_created": True}
