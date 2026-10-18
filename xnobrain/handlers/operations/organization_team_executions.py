"""Private organization execution HTTP translation."""


def operations(handler, request, body):
    return {
        "organization_team_execution": (
            lambda: handler.service.organization_team_executions.handle(request, body),
            "organization team execution",
            200,
        ),
    }
