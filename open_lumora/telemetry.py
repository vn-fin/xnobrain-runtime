"""Safe OpenTelemetry setup for the unified FastAPI process."""

from __future__ import annotations

import logging
import os


def configure(app) -> None:
    """Instrument metadata only; prompts, responses, files, and credentials stay out."""
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        logging.getLogger("open_lumora.telemetry").warning("OpenTelemetry packages are unavailable")
        return

    endpoint = str(os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") or "").strip()
    provider = TracerProvider(resource=Resource.create({
        "service.name": "open-lumora",
        "service.version": os.getenv("OPEN_LUMORA_VERSION", "dev"),
        "deployment.environment": os.getenv("START_MODE", "local"),
    }))
    if endpoint:
        exporter = OTLPSpanExporter(endpoint=endpoint, insecure=endpoint.startswith("http://"))
        provider.add_span_processor(BatchSpanProcessor(exporter, max_queue_size=2048, max_export_batch_size=256))
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(
        app,
        excluded_urls="/api/v1/health",
        http_capture_headers_server_request="",
        http_capture_headers_server_response="",
    )
    HTTPXClientInstrumentor().instrument()
