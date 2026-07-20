#!/usr/bin/env python3
"""Launch Hermes gateway with the OSS API adapter installed."""

import sys

from api_extension import install


def main() -> None:
    install()
    from hermes_cli.main import main as hermes_main

    # The gateway command is a management group; `run` is the foreground
    # process mode required by Docker and Incus service supervisors.
    sys.argv = [sys.argv[0], "gateway", "run", *sys.argv[1:]]
    hermes_main()


if __name__ == "__main__":
    main()
