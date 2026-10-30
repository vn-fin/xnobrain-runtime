"""Private personal execution HTTP translation."""


def operations(handler, request, body):
    return {
        "personal_delegated_execution": (
            lambda: handler.service.personal_delegated_executions.handle(request, body),
            "personal delegated execution",
            200,
        )
    }
