def operations(handler, request, body):
    return {
        "hosted_bootstrap": (
            lambda: handler.service.hosted.bootstrap(body),
            "hosted package bootstrapped",
            200,
        ),
        "hosted_execute": (
            lambda: handler.service.hosted.execute(body),
            "hosted execution complete",
            200,
        ),
        "hosted_backup": (lambda: handler.service.hosted.backup(), "hosted backup created", 200),
        "hosted_restore": (
            lambda: handler.service.hosted.restore(body),
            "hosted backup restored",
            200,
        ),
    }
