"""Skill Doctor lifecycle and workflow-launch route declarations."""

from ..models import (
    SkillDoctorApply,
    SkillDoctorLaunch,
    SkillDoctorLaunchRecord,
    SkillDoctorPlanCreate,
    SkillDoctorPlanRecord,
    SkillDoctorReportCreate,
    SkillDoctorReportRecord,
    SkillDoctorRollback,
)
from .definition import route

_ROOT = "/agents/{agent_id}/skill-doctor"
ROUTES = (
    route(
        "POST",
        f"{_ROOT}/launches",
        "skill_doctor_launch",
        SkillDoctorLaunch,
        tags=("Skills",),
        response_data=SkillDoctorLaunchRecord,
    ),
    route(
        "GET",
        f"{_ROOT}/launches/{{session_id}}",
        "skill_doctor_launch_get",
        tags=("Skills",),
        response_data=SkillDoctorLaunchRecord,
    ),
    route(
        "POST",
        f"{_ROOT}/reports",
        "skill_doctor_reports_create",
        SkillDoctorReportCreate,
        tags=("Skills",),
        response_data=SkillDoctorReportRecord,
    ),
    route(
        "GET",
        f"{_ROOT}/reports/{{report_id}}",
        "skill_doctor_reports_get",
        tags=("Skills",),
        response_data=SkillDoctorReportRecord,
    ),
    route(
        "POST",
        f"{_ROOT}/reports/{{report_id}}/plans",
        "skill_doctor_plans_create",
        SkillDoctorPlanCreate,
        tags=("Skills",),
        response_data=SkillDoctorPlanRecord,
    ),
    route(
        "POST",
        f"{_ROOT}/plans/{{plan_id}}/apply",
        "skill_doctor_plans_apply",
        SkillDoctorApply,
        tags=("Skills",),
        response_data=SkillDoctorPlanRecord,
    ),
    route(
        "POST",
        f"{_ROOT}/operations/{{plan_id}}/rollback",
        "skill_doctor_operations_rollback",
        SkillDoctorRollback,
        tags=("Skills",),
        response_data=SkillDoctorPlanRecord,
    ),
)
