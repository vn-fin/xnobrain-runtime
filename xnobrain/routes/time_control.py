"""Private typed FT0013 Time Control Runtime endpoints."""

from ..models.time_control import (
    TimeAdapterResult,
    TimeAdapterState,
    TimeMigrationPreviewRequest,
    TimeMigrationPreviewResult,
    TimeScheduleMigrateRequest,
    TimeSettingsApplyRequest,
)
from .definition import route

ROUTES = (
    route(
        "GET",
        "/system/time-control",
        "time_control_observe",
        tags=("Time Control",),
        response_data=TimeAdapterState,
    ),
    route(
        "POST",
        "/system/time-control/schedule-migration-preview",
        "time_control_schedule_migration_preview",
        TimeMigrationPreviewRequest,
        tags=("Time Control",),
        response_data=TimeMigrationPreviewResult,
    ),
    route(
        "POST",
        "/system/time-control/apply",
        "time_control_apply",
        TimeSettingsApplyRequest,
        tags=("Time Control",),
        response_data=TimeAdapterResult,
    ),
    route(
        "POST",
        "/system/time-control/schedule-migrate",
        "time_control_schedule_migrate",
        TimeScheduleMigrateRequest,
        tags=("Time Control",),
        response_data=TimeAdapterResult,
    ),
)
