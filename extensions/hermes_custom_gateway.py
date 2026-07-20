#!/usr/bin/env python3
"""Launch Hermes gateway with the local API adapter extension installed."""

import sys

from api_extension import install


def main() -> None:
    install()
    from hermes_cli.main import main as hermes_main

    sys.argv = [sys.argv[0], "gateway", *sys.argv[1:]]
    hermes_main()


if __name__ == "__main__":
    main()
