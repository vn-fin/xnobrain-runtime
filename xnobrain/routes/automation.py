"""Automation API route declarations."""

from ..models import CronBlueprintInstantiate, CronCreate, CronDeliveryTargetCreate
from .definition import route

ROUTES = (
    route("GET", "/cron/jobs", "cron_list", tags=("Cron",)),
    route("GET", "/cron/jobs/{job_id}", "cron_get", tags=("Cron",)),
    route("POST", "/cron/jobs", "cron_create", CronCreate, tags=("Cron",)),
    route("POST", "/cron/jobs/{job_id}/pause", "cron_pause", tags=("Cron",)),
    route("POST", "/cron/jobs/{job_id}/resume", "cron_resume", tags=("Cron",)),
    route("POST", "/cron/jobs/{job_id}/run", "cron_run", tags=("Cron",)),
    route("DELETE", "/cron/jobs/{job_id}", "cron_delete", tags=("Cron",)),
    route("GET", "/cron/blueprints", "cron_blueprints", tags=("Cron",)),
    route(
        "POST",
        "/cron/blueprints/instantiate",
        "cron_blueprint_instantiate",
        CronBlueprintInstantiate,
        tags=("Cron",),
    ),
    route("GET", "/cron/delivery-targets", "cron_delivery_targets", tags=("Cron",)),
    route("GET", "/cron/jobs/{job_id}/delivery-targets", "cron_job_targets_list", tags=("Cron",)),
    route(
        "POST",
        "/cron/jobs/{job_id}/delivery-targets",
        "cron_job_target_add",
        CronDeliveryTargetCreate,
        tags=("Cron",),
    ),
    route(
        "DELETE",
        "/cron/jobs/{job_id}/delivery-targets/{target_id}",
        "cron_job_target_remove",
        tags=("Cron",),
    ),
    route("POST", "/cron/jobs/{job_id}/trigger", "cron_trigger", tags=("Cron",)),
    route("GET", "/cron/jobs/{job_id}/runs", "cron_runs", tags=("Cron",)),
    route("GET", "/notifications", "notifications", tags=("Cron",)),
    route(
        "POST", "/notifications/{notification_id}/resolve", "notification_resolve", tags=("Cron",)
    ),
)
