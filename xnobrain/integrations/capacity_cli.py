"""Admission for each top-level managed CLI turn, before workspace activity."""

from __future__ import annotations

import sys
import time
import uuid
from functools import wraps

from ..repositories.custom_page_locks import acquire, release
from .native_admission import NativeAdmission
from .rebalance_cli import cli_activity, data_root
from .run_admission import admitted_root, current_admission, managed


def install_agent_admission(agent_class=None):
    if not managed():
        return
    if agent_class is None:
        from run_agent import AIAgent

        agent_class = AIAgent
    original = agent_class.run_conversation
    if getattr(original, "_capacity_wrapped", False) is True:
        return

    @wraps(original)
    def run(agent, *args, **kwargs):
        if current_admission() is not None:
            return original(agent, *args, **kwargs)
        gate = NativeAdmission(data_root(), uuid.uuid4().hex, "cli")
        owner = acquire(gate.root, ".capacity-owner-" + gate.id + ".lock", shared=False)
        started = False
        try:
            announced = False
            while not gate.poll():
                if not announced:
                    print(
                        "Waiting for available capacity. Press Ctrl+C to cancel.", file=sys.stderr
                    )
                    announced = True
                time.sleep(5)
            # Waiting holds no maintenance/execution lease. Recheck maintenance
            # only once a grant can actually be used.
            with cli_activity():
                gate.started()
                started = True
                with admitted_root(gate.admission):
                    return original(agent, *args, **kwargs)
        finally:
            try:
                if gate.admission:
                    gate.finish() if started else gate.abort()
                else:
                    gate.close()
            finally:
                release(owner)

    run._capacity_wrapped = True
    agent_class.run_conversation = run


def main():
    install_agent_admission()
    from hermes_cli.main import main as native_main

    native_main()


if __name__ == "__main__":
    main()
