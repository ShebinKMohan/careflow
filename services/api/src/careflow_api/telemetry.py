"""OpenTelemetry baseline: a TracerProvider (service.name careflow-api) and FastAPI spans.

Console exporter by default; OTLP when OTEL_EXPORTER_OTLP_ENDPOINT is set (Langfuse in M5-05).
Spans carry method, route and status only: never a name, a date of birth or free text.
"""

from __future__ import annotations

import os
from typing import IO

from fastapi import FastAPI
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
    SpanExporter,
)


def build_tracer_provider(
    *, exporter: SpanExporter | None = None, console_out: IO[str] | None = None
) -> TracerProvider:
    provider = TracerProvider(resource=Resource.create({"service.name": "careflow-api"}))
    processor: SpanProcessor
    if exporter is not None:
        processor = SimpleSpanProcessor(exporter)
    elif os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        processor = BatchSpanProcessor(OTLPSpanExporter())
    else:
        console = ConsoleSpanExporter(out=console_out) if console_out else ConsoleSpanExporter()
        processor = SimpleSpanProcessor(console)
    provider.add_span_processor(processor)
    return provider


def instrument(app: FastAPI, provider: TracerProvider) -> None:
    # Health probes would flood the console, and a WebSocket URL carries its one-time ticket
    # (M1-11): neither is traced. Everything else is.
    FastAPIInstrumentor.instrument_app(app, tracer_provider=provider, excluded_urls="health,v1/ws/")
