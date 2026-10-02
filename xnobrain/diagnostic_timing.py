"""Metadata-only timing for model catalog and reasoning diagnostics."""

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from time import perf_counter
from uuid import uuid4

_LOG = logging.getLogger(__name__)
_DIAGNOSTIC_ID = ContextVar("reasoning_diagnostic_id", default=None)


@contextmanager
def timing(phase):
    """Correlate nested phases without recording arguments or exception messages."""
    token = None
    if _DIAGNOSTIC_ID.get() is None:
        token = _DIAGNOSTIC_ID.set(uuid4().hex)
    started = perf_counter()
    error_type = None
    try:
        yield
    except BaseException as error:
        error_type = type(error).__name__
        raise
    finally:
        try:
            _LOG.info(
                "Reasoning timing",
                extra={
                    "event": "reasoning_timing",
                    "diagnostic_id": _DIAGNOSTIC_ID.get(),
                    "phase": phase,
                    "duration_ms": round((perf_counter() - started) * 1000, 3),
                    "error": error_type,
                },
            )
        finally:
            if token is not None:
                _DIAGNOSTIC_ID.reset(token)


def timed(phase):
    """Measure async operations, including failures and cancellation."""

    def decorate(function):
        @wraps(function)
        async def wrapped(*args, **kwargs):
            with timing(phase):
                return await function(*args, **kwargs)

        return wrapped

    return decorate
