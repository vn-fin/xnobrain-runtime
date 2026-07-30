"""Automation API route declarations."""

from ..models import CronCreate
from .definition import route

ROUTES = (
    route("GET", "/cron/jobs", "cron_list", tags=("Cron",)),
    route("GET", "/cron/jobs/{job_id}", "cron_get", tags=("Cron",)),
    route("POST", "/cron/jobs", "cron_create", CronCreate, tags=("Cron",)),
    route("POST", "/cron/jobs/{job_id}/pause", "cron_pause", tags=("Cron",)),
    route("POST", "/cron/jobs/{job_id}/resume", "cron_resume", tags=("Cron",)),
    route("POST", "/cron/jobs/{job_id}/run", "cron_run", tags=("Cron",)),
    route("DELETE", "/cron/jobs/{job_id}", "cron_delete", tags=("Cron",)),
    route("GET", "/notifications", "notifications", tags=("Cron",)),
    route("POST", "/notifications/{notification_id}/resolve", "notification_resolve", tags=("Cron",)),
)
