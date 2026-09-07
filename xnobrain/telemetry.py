"""Safe OpenTelemetry setup for the unified FastAPI process."""

from __future__ import annotations

import logging
import os


def telemetry_enabled() -> bool:
    """Return whether the explicitly opt-in local OTLP pipeline is enabled."""
    return str(os.getenv("OTEL_ENABLED", "false")).strip().lower() in {"1", "true", "yes", "on"}


def collector_endpoint() -> str | None:
    """Return the configured OTLP endpoint without making startup depend on it."""
    endpoint = str(os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") or "").strip()
    return endpoint or None


def configure(app) -> None:
    """Instrument metadata only when OTLP tracing is explicitly enabled."""
    if not telemetry_enabled():
        return
    try:
        from opentelemetry import propagate, trace
        from opentelemetry.baggage.propagation import W3CBaggagePropagator
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.grpc import GrpcAioInstrumentorServer
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        from opentelemetry.propagators.composite import CompositePropagator
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
    except ImportError:
        logging.getLogger("xnobrain.telemetry").warning("OpenTelemetry packages are unavailable")
        return

    endpoint = collector_endpoint()
    provider = TracerProvider(
        resource=Resource.create(
            {
                "service.name": os.getenv("SERVICE_NAME", "xnobrain-runtime-services"),
                "service.version": os.getenv("XNOBRAIN_VERSION", "dev"),
                "deployment.environment.name": os.getenv("DEVELOPMENT_ENVIRONMENT", "development"),
            }
        )
    )
    if endpoint:
        exporter = OTLPSpanExporter(
            endpoint=endpoint,
            insecure=str(os.getenv("OTEL_EXPORTER_OTLP_INSECURE", "true")).strip().lower()
            in {"1", "true", "yes", "on"},
        )
        provider.add_span_processor(
            BatchSpanProcessor(exporter, max_queue_size=2048, max_export_batch_size=256)
        )
    trace.set_tracer_provider(provider)
    propagate.set_global_textmap(
        CompositePropagator([TraceContextTextMapPropagator(), W3CBaggagePropagator()])
    )
    FastAPIInstrumentor.instrument_app(
        app,
        excluded_urls="/xnobrain/api/runtime/v1/health",
        http_capture_headers_server_request="",
        http_capture_headers_server_response="",
    )
    HTTPXClientInstrumentor().instrument()
    GrpcAioInstrumentorServer().instrument()
    try:
        from opentelemetry.instrumentation.aiohttp_client import AioHttpClientInstrumentor

        AioHttpClientInstrumentor().instrument()
    except ImportError:
        logging.getLogger("xnobrain.telemetry").warning(
            "aiohttp OpenTelemetry instrumentation is unavailable"
        )
