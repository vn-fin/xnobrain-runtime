"""Safe OpenTelemetry setup for the unified FastAPI process."""

from __future__ import annotations

import logging
import os
from urllib.parse import urlparse


def telemetry_enabled() -> bool:
    """Return whether the explicitly opt-in local OTLP pipeline is enabled."""
    return str(os.getenv("OTEL_ENABLED", "false")).strip().lower() in {"1", "true", "yes", "on"}


def local_collector_endpoint() -> str | None:
    """Accept only the Compose collector or loopback collector endpoints."""
    endpoint = str(os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") or "").strip()
    parsed = urlparse(endpoint if "://" in endpoint else f"//{endpoint}")
    if parsed.hostname in {"otel-collector", "localhost", "127.0.0.1", "::1"}:
        return endpoint
    if endpoint:
        logging.getLogger("brain4all.telemetry").warning("Ignoring non-local OTLP endpoint")
    return None


def configure(app) -> None:
    """Instrument metadata only when the local collector is explicitly enabled."""
    if not telemetry_enabled():
        return
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        logging.getLogger("brain4all.telemetry").warning("OpenTelemetry packages are unavailable")
        return

    endpoint = local_collector_endpoint()
    provider = TracerProvider(resource=Resource.create({
        "service.name": "brain4all",
        "service.version": os.getenv("BRAIN4ALL_VERSION", "dev"),
        "deployment.environment": "local",
    }))
    if endpoint:
        exporter = OTLPSpanExporter(endpoint=endpoint, insecure=endpoint.startswith(("http://", "otel-collector", "localhost", "127.0.0.1")))
        provider.add_span_processor(BatchSpanProcessor(exporter, max_queue_size=2048, max_export_batch_size=256))
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(
        app,
        excluded_urls="/api/v1/health",
        http_capture_headers_server_request="",
        http_capture_headers_server_response="",
    )
    HTTPXClientInstrumentor().instrument()
