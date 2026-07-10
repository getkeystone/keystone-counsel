"""OpenTelemetry setup for Keystone Counsel.

Same pattern as keystone-engage. GenAI semantic conventions for
model calls. Service name: keystone-counsel.
"""

from __future__ import annotations

import logging

from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

from keystone_counsel import __version__

logger = logging.getLogger(__name__)

_tracer: trace.Tracer | None = None


def setup_telemetry(app) -> trace.Tracer:
    global _tracer
    resource = Resource.create(
        {
            "service.name": "keystone-counsel",
            "service.version": __version__,
            "deployment.environment": "development",
        }
    )
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app)
    _tracer = trace.get_tracer("keystone-counsel", __version__)
    logger.info("OTel telemetry configured for keystone-counsel")
    return _tracer


def get_tracer() -> trace.Tracer:
    if _tracer is None:
        return trace.get_tracer("keystone-counsel")
    return _tracer
