from ..models.hosted import (
    HostedBackupRequest,
    HostedBootstrapRequest,
    HostedExecuteRequest,
    HostedRestoreRequest,
)
from .definition import route

ROUTES = (
    route(
        "POST", "/hosted/bootstrap", "hosted_bootstrap", HostedBootstrapRequest, tags=("Hosted",)
    ),
    route("POST", "/hosted/execute", "hosted_execute", HostedExecuteRequest, tags=("Hosted",)),
    route("POST", "/hosted/backup", "hosted_backup", HostedBackupRequest, tags=("Hosted",)),
    route("POST", "/hosted/restore", "hosted_restore", HostedRestoreRequest, tags=("Hosted",)),
)
