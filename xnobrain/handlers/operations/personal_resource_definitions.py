"""Translate private snapshot requests."""


def operations(handler, request, body):
    return {
        "personal_resource_definition": (
            lambda: handler.service.personal_resource_definitions.handle(request, body),
            "personal resource definition",
            200,
        ),
        "personal_agent_team_save": (
            lambda: handler.service.personal_resource_definitions.save_team(request, body),
            "personal agent team saved",
            200,
        ),
    }
