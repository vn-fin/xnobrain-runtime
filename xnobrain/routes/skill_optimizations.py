"""Skill optimization lifecycle API declarations."""

from ..models import (
    SkillOptimizationApply,
    SkillOptimizationApprovalCreate,
    SkillOptimizationCancel,
    SkillOptimizationCreate,
    SkillOptimizationEvaluationCreate,
    SkillOptimizationRollback,
)
from .definition import route

_ROOT = "/agents/{agent_id}/skill-optimizations"
ROUTES = (
    route("POST", _ROOT, "skill_optimizations_create", SkillOptimizationCreate, tags=("Skills",)),
    route("GET", f"{_ROOT}/{{optimization_id}}", "skill_optimizations_get", tags=("Skills",)),
    route(
        "POST",
        f"{_ROOT}/{{optimization_id}}/evaluations",
        "skill_optimizations_evaluate",
        SkillOptimizationEvaluationCreate,
        tags=("Skills",),
    ),
    route(
        "POST",
        f"{_ROOT}/{{optimization_id}}/approvals",
        "skill_optimizations_approve",
        SkillOptimizationApprovalCreate,
        tags=("Skills",),
    ),
    route(
        "POST",
        f"{_ROOT}/{{optimization_id}}/apply",
        "skill_optimizations_apply",
        SkillOptimizationApply,
        tags=("Skills",),
    ),
    route(
        "POST",
        f"{_ROOT}/{{optimization_id}}/cancel",
        "skill_optimizations_cancel",
        SkillOptimizationCancel,
        tags=("Skills",),
    ),
    route(
        "POST",
        f"{_ROOT}/{{optimization_id}}/rollback",
        "skill_optimizations_rollback",
        SkillOptimizationRollback,
        tags=("Skills",),
    ),
)
