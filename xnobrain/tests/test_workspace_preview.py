"""Workspace Office preview conversion and cache tests."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, ZipFile

from xnobrain.services.workspace_preview import (
    WorkspacePreviewError,
    WorkspacePreviewService,
)


class WorkspacePreviewTests(unittest.TestCase):
    def test_conversion_timeout_is_configurable_and_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with patch.dict(
                "os.environ",
                {"WORKSPACE_PREVIEW_TIMEOUT_SECONDS": "480"},
            ):
                service = WorkspacePreviewService(Path(temporary) / "cache")

            self.assertEqual(service.conversion_timeout_seconds, 480)

    def test_office_document_is_converted_once_and_cached(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "report.xlsx"
            source.write_bytes(b"test-workbook")
            service = WorkspacePreviewService(root / "cache")

            def convert(command, **_kwargs):
                output_root = Path(command[command.index("--outdir") + 1])
                (output_root / "document.pdf").write_bytes(b"%PDF-1.7\npreview")
                return type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})()

            with (
                patch("xnobrain.services.workspace_preview.shutil.which", return_value="/usr/bin/soffice"),
                patch("xnobrain.services.workspace_preview.subprocess.run", side_effect=convert) as run,
            ):
                first = service.preview(source)
                second = service.preview(source)

            self.assertEqual(first.content, b"%PDF-1.7\npreview")
            self.assertEqual(first.media_type, "application/pdf")
            self.assertEqual(first.filename, "report.pdf")
            self.assertEqual(second, first)
            run.assert_called_once()

    def test_workbook_is_normalized_once_and_cached(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "report.xlsx"
            source.write_bytes(b"prefixed-ooxml-workbook")
            service = WorkspacePreviewService(root / "cache")

            def convert(command, **_kwargs):
                output_root = Path(command[command.index("--outdir") + 1])
                with ZipFile(output_root / "document.xlsx", "w", ZIP_DEFLATED) as archive:
                    archive.writestr("[Content_Types].xml", "<Types/>")
                    archive.writestr("xl/workbook.xml", "<workbook/>")
                return type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})()

            with (
                patch("xnobrain.services.workspace_preview.shutil.which", return_value="/usr/bin/soffice"),
                patch("xnobrain.services.workspace_preview.subprocess.run", side_effect=convert) as run,
            ):
                first = service.workbook(source)
                second = service.workbook(source)

            self.assertTrue(first.content.startswith(b"PK"))
            self.assertEqual(
                first.media_type,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
            self.assertEqual(first.filename, "report.xlsx")
            self.assertEqual(second, first)
            run.assert_called_once()

    def test_macro_enabled_workbook_is_normalized_as_xlsx(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "forecast.xlsm"
            source.write_bytes(b"macro-enabled-workbook")
            service = WorkspacePreviewService(root / "cache")

            def convert(command, **_kwargs):
                self.assertIn("document.xlsm", command[-1])
                output_root = Path(command[command.index("--outdir") + 1])
                with ZipFile(output_root / "document.xlsx", "w", ZIP_DEFLATED) as archive:
                    archive.writestr("[Content_Types].xml", "<Types/>")
                    archive.writestr("xl/workbook.xml", "<workbook/>")
                return type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})()

            with (
                patch("xnobrain.services.workspace_preview.shutil.which", return_value="/usr/bin/soffice"),
                patch("xnobrain.services.workspace_preview.subprocess.run", side_effect=convert),
            ):
                result = service.workbook(source)

            self.assertTrue(result.content.startswith(b"PK"))
            self.assertEqual(result.filename, "forecast.xlsx")

    def test_unsupported_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "archive.zip"
            source.write_bytes(b"zip")
            service = WorkspacePreviewService(root / "cache")

            with self.assertRaises(WorkspacePreviewError) as raised:
                service.preview(source)

            self.assertEqual(raised.exception.status, 415)
            self.assertEqual(raised.exception.code, "workspace_preview_unsupported")
