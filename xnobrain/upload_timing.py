"""Bounded upload diagnostics and W3C correlation without an exporter dependency."""

from __future__ import annotations

import logging
import re
import time

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

_LOGGER = logging.getLogger(__name__)
_UPLOAD_ID = re.compile(r"[a-f0-9]{32}")
_PART = re.compile(r"[0-9]{1,10}")
_PROPAGATOR = TraceContextTextMapPropagator()


def relay_trace_headers(metadata):
    """Use the active instrumented span, otherwise validated private RPC context.

    Browser-carried headers are removed by the relay. Baggage is not forwarded.
    """
    current = trace.get_current_span().get_span_context()
    context = None if current.is_valid else _PROPAGATOR.extract(metadata, context=Context())
    headers = {}
    _PROPAGATOR.inject(headers, context=context)
    return headers


class UploadTiming:
    """One record per completed or failed phase, never including request content."""

    def __init__(self, upload_id, part_number, headers):
        self.upload_id = str(upload_id)
        self.part_number = str(part_number)
        self.enabled = bool(
            _UPLOAD_ID.fullmatch(self.upload_id) and _PART.fullmatch(self.part_number)
        )
        current = trace.get_current_span().get_span_context()
        self.span = (
            current
            if current.is_valid
            else trace.get_current_span(
                _PROPAGATOR.extract(headers, context=Context())
            ).get_span_context()
        )

    def record(self, stage, started, *, failed=False):
        if not self.enabled:
            return
        fields = {
            "event": "bundle_upload_timing",
            "stage": stage,
            "upload_id": self.upload_id,
            "part_number": int(self.part_number),
            "duration_ms": round((time.monotonic() - started) * 1000, 3),
            "failed": failed,
        }
        if self.span.is_valid:
            fields["trace_id"] = format(self.span.trace_id, "032x")
            fields["span_id"] = format(self.span.span_id, "016x")
        _LOGGER.info("Bundle upload stage completed", extra=fields)
