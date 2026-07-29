"""Safe, cached workspace previews for browser-incompatible Office files."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading


OFFICE_EXTENSIONS = frozenset({
    ".doc", ".docx", ".odt", ".rtf",
    ".xls", ".xlsx", ".ods",
    ".ppt", ".pptx", ".odp",
})
MAX_PREVIEW_SOURCE_BYTES = 25 * 1024 * 1024
MAX_PREVIEW_BYTES = 50 * 1024 * 1024
MAX_CACHE_FILES = 256


class WorkspacePreviewError(ValueError):
    """An expected workspace preview error."""

    def __init__(self, message: str, *, status: int = 400, code: str = "workspace_preview_failed"):
        super().__init__(message)
        self.status = status
        self.code = code


@dataclass(frozen=True)
class WorkspacePreview:
    content: bytes
    media_type: str
    filename: str


class WorkspacePreviewService:
    """Convert Office documents to PDF without exposing an active document."""

    def __init__(self, cache_root: str | Path):
        self.cache_root = Path(cache_root).resolve()
        self.cache_root.mkdir(parents=True, exist_ok=True, mode=0o750)
        self._lock = threading.Lock()

    def preview(self, source: str | Path) -> WorkspacePreview:
        source = Path(source)
        suffix = source.suffix.lower()
        if suffix not in OFFICE_EXTENSIONS:
            raise WorkspacePreviewError(
                f"preview is not supported for {suffix or 'this file type'}",
                status=415,
                code="workspace_preview_unsupported",
            )
        if not source.is_file():
            raise WorkspacePreviewError(
                "workspace file was not found",
                status=404,
                code="workspace_path_not_found",
            )
        if source.stat().st_size > MAX_PREVIEW_SOURCE_BYTES:
            raise WorkspacePreviewError(
                f"file is too large to preview (max {MAX_PREVIEW_SOURCE_BYTES} bytes)",
                status=413,
                code="workspace_preview_too_large",
            )

        digest = self._digest(source)
        cached = self.cache_root / f"{digest}.pdf"
        with self._lock:
            if not self._valid_pdf(cached):
                self._convert(source, cached)
                self._prune()
            content = cached.read_bytes()
        return WorkspacePreview(
            content=content,
            media_type="application/pdf",
            filename=f"{source.stem}.pdf",
        )

    @staticmethod
    def _digest(source: Path) -> str:
        digest = hashlib.sha256()
        digest.update(b"brain4all-office-preview-v1\0")
        digest.update(source.suffix.lower().encode("utf-8"))
        with source.open("rb") as file:
            while chunk := file.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()

    def _convert(self, source: Path, cached: Path) -> None:
        executable = shutil.which("soffice") or shutil.which("libreoffice")
        if not executable:
            raise WorkspacePreviewError(
                "LibreOffice is not installed in the runtime",
                status=503,
                code="workspace_preview_unavailable",
            )
        with tempfile.TemporaryDirectory(prefix="brain4all-preview-") as temporary:
            temporary_root = Path(temporary)
            input_path = temporary_root / f"document{source.suffix.lower()}"
            output_root = temporary_root / "output"
            profile_root = temporary_root / "libreoffice-profile"
            output_root.mkdir(mode=0o750)
            profile_root.mkdir(mode=0o750)
            shutil.copyfile(source, input_path)
            try:
                completed = subprocess.run(
                    [
                        executable,
                        "--headless",
                        f"-env:UserInstallation={profile_root.as_uri()}",
                        "--convert-to",
                        "pdf",
                        "--outdir",
                        str(output_root),
                        str(input_path),
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
            except subprocess.TimeoutExpired as error:
                raise WorkspacePreviewError(
                    "document preview conversion timed out",
                    status=504,
                ) from error
            output_path = output_root / "document.pdf"
            if completed.returncode != 0 or not self._valid_pdf(output_path):
                detail = (completed.stderr or completed.stdout or "conversion failed").strip()
                raise WorkspacePreviewError(
                    f"document preview conversion failed: {detail[:240]}",
                    status=422,
                )
            if output_path.stat().st_size > MAX_PREVIEW_BYTES:
                raise WorkspacePreviewError(
                    "converted preview is too large",
                    status=413,
                    code="workspace_preview_too_large",
                )
            staged = self.cache_root / f".{cached.name}.{os.getpid()}.tmp"
            shutil.copyfile(output_path, staged)
            os.chmod(staged, 0o640)
            os.replace(staged, cached)

    @staticmethod
    def _valid_pdf(path: Path) -> bool:
        try:
            if not path.is_file() or path.stat().st_size < 5:
                return False
            with path.open("rb") as file:
                return file.read(5) == b"%PDF-"
        except OSError:
            return False

    def _prune(self) -> None:
        files = sorted(
            (item for item in self.cache_root.glob("*.pdf") if item.is_file()),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        for expired in files[MAX_CACHE_FILES:]:
            try:
                expired.unlink()
            except OSError:
                pass
