"""Portability API route declarations."""

from ..models import (
    BundleExport,
    BundleImportTask,
    BundleUploadApply,
    BundleUploadComplete,
    BundleUploadStart,
)
from .definition import route

ROUTES = (
    route(
        "POST",
        "/community/snapshots/{agent_id}/export",
        "community_snapshot_export",
        special="community_snapshot",
        tags=("Portability",),
        include_in_schema=False,
    ),
    route(
        "POST",
        "/community/snapshots/import",
        "community_snapshot_import",
        special="community_snapshot",
        tags=("Portability",),
        include_in_schema=False,
    ),
    route(
        "POST",
        "/bundles/import-tasks",
        "bundle_task_import",
        BundleImportTask,
        special="bundle_task",
        tags=("Portability",),
    ),
    route(
        "GET",
        "/bundles/task-exports/{transfer_id}/parts/{part_number}",
        "bundle_task_part",
        special="bundle_part",
        tags=("Portability",),
    ),
    route(
        "DELETE",
        "/bundles/task-exports/{transfer_id}",
        "bundle_task_delete",
        special="bundle_task",
        tags=("Portability",),
    ),
    route(
        "POST",
        "/bundles/export-tasks",
        "bundle_task_create",
        BundleExport,
        special="bundle_task",
        tags=("Portability",),
    ),
    route(
        "GET", "/bundles/tasks", "bundle_task_list", special="bundle_task", tags=("Portability",)
    ),
    route(
        "GET",
        "/bundles/tasks/{task_id}",
        "bundle_task_get",
        special="bundle_task",
        tags=("Portability",),
    ),
    route(
        "POST", "/bundles/export", "bundle_export", BundleExport, "bundle_export", ("Portability",)
    ),
    route(
        "POST", "/bundles/inspect", "bundle_inspect", special="bundle_upload", tags=("Portability",)
    ),
    route(
        "POST", "/bundles/dry-run", "bundle_dry_run", special="bundle_upload", tags=("Portability",)
    ),
    route("POST", "/bundles/apply", "bundle_apply", special="bundle_upload", tags=("Portability",)),
    route("POST", "/bundles/exports", "bundle_export_start", BundleExport, tags=("Portability",)),
    route(
        "GET",
        "/bundles/exports/{transfer_id}/parts/{part_number}",
        "bundle_export_part",
        special="bundle_part",
        tags=("Portability",),
    ),
    route(
        "DELETE", "/bundles/exports/{transfer_id}", "bundle_export_delete", tags=("Portability",)
    ),
    route(
        "POST", "/bundles/uploads", "bundle_upload_start", BundleUploadStart, tags=("Portability",)
    ),
    route(
        "PUT",
        "/bundles/uploads/{transfer_id}/parts/{part_number}",
        "bundle_upload_part",
        special="bundle_part",
        tags=("Portability",),
    ),
    route(
        "POST",
        "/bundles/uploads/{transfer_id}/complete",
        "bundle_upload_complete",
        BundleUploadComplete,
        tags=("Portability",),
    ),
    route(
        "POST",
        "/bundles/uploads/{transfer_id}/apply",
        "bundle_upload_apply",
        BundleUploadApply,
        tags=("Portability",),
    ),
    route(
        "DELETE", "/bundles/uploads/{transfer_id}", "bundle_upload_delete", tags=("Portability",)
    ),
)
