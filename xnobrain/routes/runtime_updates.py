"""Private typed Runtime update lifecycle endpoints."""

from ..models import (
    RuntimeRolloutDrain,
    RuntimeRolloutPostVerify,
    RuntimeRolloutPreflight,
    RuntimeRolloutReadiness,
    RuntimeRolloutRequest,
    RuntimeUpdateCheckpoint,
    RuntimeUpdateDrain,
    RuntimeUpdatePostVerify,
    RuntimeUpdatePreflight,
    RuntimeUpdateReadiness,
    RuntimeUpdateRecovery,
    RuntimeUpdateRequest,
)
from .definition import route

ROUTES = (
    route(
        "POST",
        "/system/update/preflight",
        "runtime_update_preflight",
        RuntimeUpdatePreflight,
        tags=("Runtime updates",),
    ),
    route(
        "POST",
        "/system/update/drain",
        "runtime_update_drain",
        RuntimeUpdateDrain,
        tags=("Runtime updates",),
    ),
    route(
        "POST",
        "/system/update/checkpoint",
        "runtime_update_checkpoint",
        RuntimeUpdateCheckpoint,
        tags=("Runtime updates",),
    ),
    route(
        "POST",
        "/system/update/readiness",
        "runtime_update_readiness",
        RuntimeUpdateReadiness,
        tags=("Runtime updates",),
    ),
    route(
        "POST",
        "/system/update/post-verify",
        "runtime_update_post_verify",
        RuntimeUpdatePostVerify,
        tags=("Runtime updates",),
    ),
    route(
        "POST",
        "/system/update/recovery",
        "runtime_update_recovery",
        RuntimeUpdateRecovery,
        tags=("Runtime updates",),
    ),
    route(
        "POST",
        "/system/update/resume",
        "runtime_update_resume",
        RuntimeUpdateRequest,
        tags=("Runtime updates",),
    ),
)

# The same maintenance service owns both adapters. The original OCI contract
# remains strict; only these explicitly versioned routes accept Incus targets.
ROUTES += tuple(
    route(
        "POST",
        "/system/update/v2/" + action,
        operation,
        model,
        tags=("Runtime updates",),
    )
    for action, operation, model in (
        ("preflight", "runtime_update_preflight", RuntimeRolloutPreflight),
        ("drain", "runtime_update_drain", RuntimeRolloutDrain),
        ("checkpoint", "runtime_update_checkpoint", RuntimeRolloutRequest),
        ("readiness", "runtime_update_readiness", RuntimeRolloutReadiness),
        ("post-verify", "runtime_update_post_verify", RuntimeRolloutPostVerify),
        ("resume", "runtime_update_resume", RuntimeRolloutRequest),
    )
)
