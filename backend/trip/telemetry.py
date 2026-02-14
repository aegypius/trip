"""
OpenTelemetry instrumentation configuration.

This module provides distributed tracing and metrics for the TRIP backend.
It automatically instruments:
- FastAPI endpoints (HTTP requests/responses)
- SQLAlchemy database queries
- HTTPX external HTTP calls

Features:
- Captures error response bodies for debugging (4xx/5xx status codes)
- Extracts structured validation errors from FastAPI
- Supports OTLP export to Jaeger, Tempo, or other backends
- Optional console export for local debugging

Note: OpenTelemetry packages are optional. If not installed, this module
      will gracefully disable all telemetry features.
"""
import json
import logging

from fastapi import Request
from fastapi.responses import FileResponse
from starlette.responses import Response, StreamingResponse

from . import __version__
from .config import settings

logger = logging.getLogger(__name__)

# Try to import OpenTelemetry packages - they are optional
try:
    from opentelemetry import trace, metrics
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
    from opentelemetry.sdk.resources import Resource, SERVICE_NAME, SERVICE_VERSION
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader, ConsoleMetricExporter
    
    OTEL_AVAILABLE = True
except ImportError:
    OTEL_AVAILABLE = False
    logger.warning("OpenTelemetry packages not installed. Telemetry disabled.")


def init_telemetry(app) -> None:
    """
    Initialize OpenTelemetry instrumentation for the FastAPI application.
    
    Configures:
    - Trace and metric providers with service metadata
    - OTLP exporters for sending telemetry data
    - Auto-instrumentation for FastAPI, HTTPX, and SQLAlchemy
    
    Args:
        app: FastAPI application instance
    """
    if not OTEL_AVAILABLE:
        return
    
    if not settings.OTEL_ENABLED:
        logger.info("OpenTelemetry disabled via configuration")
        return
    
    logger.info(f"Initializing OpenTelemetry (endpoint: {settings.OTEL_EXPORTER_OTLP_ENDPOINT})")
    
    # Resource identifies your service
    resource = Resource(attributes={
        SERVICE_NAME: settings.OTEL_SERVICE_NAME,
        SERVICE_VERSION: __version__,
        "deployment.environment": settings.OTEL_ENVIRONMENT,
    })
    
    # Setup Tracing
    _setup_tracing(resource)
    
    # Setup Metrics (optional)
    if settings.OTEL_METRICS_ENABLED:
        _setup_metrics(resource)
    
    # Auto-instrument FastAPI
    FastAPIInstrumentor.instrument_app(app)
    
    # Auto-instrument HTTPX (for external HTTP calls)
    HTTPXClientInstrumentor().instrument()
    
    # Register error capture middleware
    _register_error_middleware(app)
    
    logger.info("OpenTelemetry initialized successfully")


def _register_error_middleware(app) -> None:
    """Register the error response capture middleware."""
    app.middleware("http")(capture_error_response_middleware)


def _setup_tracing(resource: Resource) -> None:
    """Configure trace provider and exporters."""
    
    # Create tracer provider
    tracer_provider = TracerProvider(resource=resource)
    
    # Add OTLP exporter (sends to Jaeger/Tempo/etc)
    if settings.OTEL_EXPORTER_OTLP_ENDPOINT:
        try:
            otlp_exporter = OTLPSpanExporter(
                endpoint=settings.OTEL_EXPORTER_OTLP_ENDPOINT,
                insecure=settings.OTEL_EXPORTER_OTLP_INSECURE,
            )
            tracer_provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
            logger.info(f"OTLP trace exporter configured for {settings.OTEL_EXPORTER_OTLP_ENDPOINT}")
        except Exception as e:
            logger.error(f"Failed to setup OTLP exporter: {e}")
    
    # Add console exporter for debugging (optional)
    if settings.OTEL_CONSOLE_EXPORTER:
        console_exporter = ConsoleSpanExporter()
        tracer_provider.add_span_processor(BatchSpanProcessor(console_exporter))
        logger.info("Console trace exporter enabled")
    
    # Set as global tracer provider
    trace.set_tracer_provider(tracer_provider)
    logger.info("Tracer provider set as global")


def _setup_metrics(resource: Resource) -> None:
    """Configure metrics provider and exporters."""
    
    # OTLP Metrics exporter
    if settings.OTEL_EXPORTER_OTLP_ENDPOINT:
        otlp_metric_exporter = OTLPMetricExporter(
            endpoint=settings.OTEL_EXPORTER_OTLP_ENDPOINT,
            insecure=settings.OTEL_EXPORTER_OTLP_INSECURE,
        )
        metric_reader = PeriodicExportingMetricReader(otlp_metric_exporter)
    else:
        # Fallback to console
        console_metric_exporter = ConsoleMetricExporter()
        metric_reader = PeriodicExportingMetricReader(console_metric_exporter)
    
    # Create meter provider
    meter_provider = MeterProvider(
        resource=resource,
        metric_readers=[metric_reader],
    )
    
    # Set as global meter provider
    metrics.set_meter_provider(meter_provider)


def instrument_sqlalchemy(engine) -> None:
    """
    Instrument SQLAlchemy engine for database tracing.
    
    Captures all SQL queries as spans with full SQL statements and timing.
    
    Args:
        engine: SQLAlchemy engine instance
    """
    if not OTEL_AVAILABLE or not settings.OTEL_ENABLED:
        return
    
    SQLAlchemyInstrumentor().instrument(
        engine=engine,
        service=settings.OTEL_SERVICE_NAME,
    )


async def capture_error_response_middleware(request: Request, call_next):
    """
    Middleware to capture and enrich error responses in OpenTelemetry spans.
    
    For HTTP 4xx/5xx responses:
    - Captures full response body (truncated to 2KB)
    - Extracts structured error information from FastAPI validation errors
    - Adds error attributes to the active span for better debugging
    
    Attributes added:
    - http.response.body: Full error response body
    - error.message: Simple error message (for string details)
    - error.validation.N.{type,loc,msg}: Structured validation errors
    
    Args:
        request: Incoming HTTP request
        call_next: Next middleware in the chain
        
    Returns:
        HTTP response (potentially recreated to include captured body)
    """
    response = await call_next(request)
    
    # Skip if OpenTelemetry not available
    if not OTEL_AVAILABLE:
        return response
    
    # Only process error responses
    if response.status_code >= 400:
        # Get the current span from the active context
        span = trace.get_current_span()
        
        if span and span.is_recording() and not isinstance(response, (StreamingResponse, FileResponse)):
            # Read response body
            body_bytes = b""
            async for chunk in response.body_iterator:
                body_bytes += chunk
            
            # Decode and truncate body
            body_str = body_bytes.decode("utf-8", errors="replace")
            if len(body_str) > 2048:
                body_str = body_str[:2048] + "... [truncated]"
            
            # Add raw body to span
            span.set_attribute("http.response.body", body_str)
            
            # Try to parse JSON and extract structured error info
            try:
                body_json = json.loads(body_str)
                if isinstance(body_json, dict):
                    # Extract FastAPI validation errors
                    if "detail" in body_json:
                        detail = body_json["detail"]
                        if isinstance(detail, str):
                            span.set_attribute("error.message", detail)
                        elif isinstance(detail, list):
                            # Validation errors array
                            for i, err in enumerate(detail[:5]):  # Limit to first 5
                                if isinstance(err, dict):
                                    span.set_attribute(f"error.validation.{i}.type", err.get("type", ""))
                                    span.set_attribute(f"error.validation.{i}.loc", str(err.get("loc", [])))
                                    span.set_attribute(f"error.validation.{i}.msg", err.get("msg", ""))
            except (json.JSONDecodeError, KeyError, TypeError):
                pass  # Not JSON or unexpected structure
            
            # Recreate response with the same body
            return Response(
                content=body_bytes,
                status_code=response.status_code,
                headers=dict(response.headers),
                media_type=response.media_type,
            )
    
    return response
