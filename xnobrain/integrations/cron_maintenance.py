"""Freeze native ticker status writes while workspace maintenance is closed."""

from __future__ import annotations

from functools import wraps
from pathlib import Path

from ..repositories.base import StoreError
from ..repositories.runtime_update_gate import admission_gate, require_admission

TICKER_WRITERS = (
    "record_ticker_heartbeat",
    "record_ticker_error",
    "clear_ticker_error",
)


def _guarded_write(root: Path, function):
    @wraps(function)
    def guarded(*args, **kwargs):
        try:
            # Ticker startup and paused ticks still write native health files.
            # Serialize their whole write with publishing maintenance; this is
            # independent housekeeping, never a descendant of an admitted task.
            with admission_gate(root):
                require_admission(root, allow_lineage=False)
                return function(*args, **kwargs)
        except StoreError:
            # Native ticker health writes are best effort. Unknown or closed
            # admission must leave the checkpointed bytes untouched too.
            return None

    guarded._xnobrain_ticker_original = function
    return guarded


def install_ticker_maintenance(root: Path) -> None:
    """Install before the upstream lifespan starts any cron ticker thread."""
    from cron import jobs

    for name in TICKER_WRITERS:
        function = getattr(jobs, name)
        original = getattr(function, "_xnobrain_ticker_original", function)
        setattr(jobs, name, _guarded_write(root, original))
