from ..community_legacy import deliver


def operations(handler, request, body):
    return {
        "marketplace_export": (
            lambda: handler.service.marketplace.export(
                request.path_params["agent_id"], body["license"]
            ),
            "marketplace package exported",
            200,
        ),
        "marketplace_install": (
            lambda: deliver(handler, request, body, "install"),
            "marketplace package installed",
            201,
        ),
        "marketplace_update": (
            lambda: deliver(handler, request, body, "update"),
            "marketplace package updated",
            200,
        ),
        "marketplace_uninstall": (
            lambda: handler.service.marketplace.uninstall(body["local_profile_id"]),
            "marketplace package uninstalled",
            200,
        ),
    }
