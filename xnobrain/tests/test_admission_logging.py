"""Admission callbacks preserve tracing and redact dependency messages."""

import unittest
from unittest.mock import patch

import httpx
from opentelemetry import propagate, trace
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from xnobrain.integrations.run_admission import POLICY_VERSION, AdmissionClient
from xnobrain.services.base import ServiceError


class AdmissionLoggingTests(unittest.IsolatedAsyncioTestCase):
    async def test_trace_header_and_redacted_failure(self):
        seen = []

        def respond(request):
            seen.append(request.headers.get("traceparent"))
            return httpx.Response(503, json={"message": "SECRET upstream body"})

        span = trace.NonRecordingSpan(
            trace.SpanContext(
                trace_id=int("0123456789abcdef0123456789abcdef", 16),
                span_id=int("0123456789abcdef", 16),
                is_remote=False,
            )
        )
        previous = propagate.get_global_textmap()
        propagate.set_global_textmap(TraceContextTextMapPropagator())
        self.addCleanup(propagate.set_global_textmap, previous)
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as transport:
            client = AdmissionClient(transport)
            with (
                patch.dict(
                    "os.environ",
                    {
                        "RUNTIME_CONTROL_URL": "https://control.test",
                        "RUNTIME_WORKSPACE_ID": "workspace",
                        "RUNTIME_INTERNAL_SERVICE_TOKEN": "SECRET",
                    },
                ),
                trace.use_span(span),
                self.assertLogs("xnobrain.integrations.run_admission", level="WARNING") as logs,
                self.assertRaises(ServiceError),
            ):
                await client.call("POST", body={"policy_version": POLICY_VERSION})
        self.assertIn("0123456789abcdef0123456789abcdef", seen[0])
        self.assertEqual(logs.records[0].http_status_code, 503)
        self.assertNotIn("SECRET", str(logs.output))
