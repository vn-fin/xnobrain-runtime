from ..models.hosted import HostedBootstrapRequest,HostedExecuteRequest,HostedBackupRequest,HostedRestoreRequest
from .definition import route
ROUTES=(route('POST','/hosted/bootstrap','hosted_bootstrap',HostedBootstrapRequest,tags=('Hosted',)),route('POST','/hosted/execute','hosted_execute',HostedExecuteRequest,tags=('Hosted',)),route('POST','/hosted/backup','hosted_backup',HostedBackupRequest,tags=('Hosted',)),route('POST','/hosted/restore','hosted_restore',HostedRestoreRequest,tags=('Hosted',)))
