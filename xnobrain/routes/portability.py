"""Portability API route declarations."""

from ..models import BundleExport, BundleUploadApply, BundleUploadComplete, BundleUploadStart
from .definition import route

ROUTES = (
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
