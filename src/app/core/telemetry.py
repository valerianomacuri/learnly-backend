"""OpenTelemetry wiring (spec 13): API and worker, OTLP/HTTP, off by default."""

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings

logger = logging.getLogger("app.telemetry")


def setup_telemetry(
    settings: Settings,
    *,
    service: str,
    app: Any | None = None,
    engine: AsyncEngine | None = None,
) -> bool:
    """Install the tracer provider and instrumentations; returns whether it ran.

    Never raises: a broken exporter must not stop the API or the worker.
    """
    if not settings.otel_enabled:
        return False
    endpoint = settings.otel_exporter_otlp_endpoint.rstrip("/") + "/v1/traces"
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider(resource=Resource.create({"service.name": service}))
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint))
        )
        trace.set_tracer_provider(provider)
        if engine is not None:
            SQLAlchemyInstrumentor().instrument(engine=engine.sync_engine)
        if app is not None:
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

            FastAPIInstrumentor.instrument_app(
                app, excluded_urls="healthz,health,readyz,metrics"
            )
    except Exception:
        logger.exception("telemetry setup failed; continuing without it")
        return False
    logger.info("telemetry enabled", extra={"otlp_endpoint": endpoint})
    return True
