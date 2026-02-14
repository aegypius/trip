# OpenTelemetry Observability

OpenTelemetry provides distributed tracing, metrics, and logging for TRIP's FastAPI backend.

## Features

### Automatic Instrumentation

- ✅ **HTTP Requests**: All FastAPI endpoints with timing and status codes
- ✅ **SQL Queries**: All SQLAlchemy queries with full SQL statements
- ✅ **External HTTP Calls**: HTTPX requests to Google Maps API, OIDC providers, etc.
- ✅ **Complete Timing**: Detailed latency breakdown
- ✅ **Error Traces**: Exceptions with stack traces and context

### Error Response Capture

For all HTTP 4xx/5xx errors, the following attributes are automatically captured:

- `http.response.body` - Full error response body (truncated to 2KB)
- `error.message` - Simple error message (for string details)
- `error.validation.N.type` - Validation error type (e.g., "missing", "type_error")
- `error.validation.N.loc` - Field location in request body
- `error.validation.N.msg` - Human-readable error message

This makes debugging FastAPI validation errors and application errors much easier in Jaeger.

Example trace hierarchy:
```
GET /api/trips/123
├─ SELECT FROM trip WHERE id=123
├─ SELECT FROM trip_day WHERE trip_id=123
├─ SELECT FROM trip_item WHERE day_id IN (...)
└─ SELECT FROM place WHERE id IN (...)
```

## Installation

OpenTelemetry dependencies are already included in `backend/trip/requirements.txt`. The telemetry module is located at `backend/trip/telemetry.py` and initializes automatically on application startup.

## Activation

### Option 1: Via configuration file

Add to `storage/config.yml`:

```yaml
# OpenTelemetry Configuration
OTEL_ENABLED: true
OTEL_SERVICE_NAME: "trip-backend"
OTEL_ENVIRONMENT: "production"  # or "development", "staging"
OTEL_EXPORTER_OTLP_ENDPOINT: "http://jaeger:4317"
OTEL_EXPORTER_OTLP_INSECURE: true
OTEL_METRICS_ENABLED: true
```

### Option 2: Via Docker environment variables

In `docker-compose.override.yml`:

```yaml
services:
  app:
    environment:
      - OTEL_ENABLED=true
      - OTEL_SERVICE_NAME=trip-backend
      - OTEL_ENVIRONMENT=production
      - OTEL_EXPORTER_OTLP_ENDPOINT=http://jaeger:4317
      - OTEL_EXPORTER_OTLP_INSECURE=true
      - OTEL_METRICS_ENABLED=true
```

### Available configuration variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OTEL_ENABLED` | `false` | Enable/disable OpenTelemetry instrumentation |
| `OTEL_SERVICE_NAME` | `trip-backend` | Service identifier in traces |
| `OTEL_ENVIRONMENT` | `production` | Environment tag (dev/staging/prod) |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://localhost:4317` | OTLP gRPC endpoint URL |
| `OTEL_EXPORTER_OTLP_INSECURE` | `true` | Use insecure gRPC (no TLS) |
| `OTEL_CONSOLE_EXPORTER` | `false` | Print traces to console (debug) |
| `OTEL_METRICS_ENABLED` | `false` | Enable metrics collection |

## Viewing traces with Jaeger

### Setup with Docker Compose

1. **Copy the configuration template**:
   ```bash
   cp docker-compose.override.yml.dist docker-compose.override.yml
   ```

2. **Edit `docker-compose.override.yml`** and uncomment the Jaeger service:
   ```yaml
   services:
     app:
       environment:
         - OTEL_ENABLED=true
         - OTEL_EXPORTER_OTLP_ENDPOINT=http://jaeger:4317
       networks:
         - default
   
     jaeger:
       image: cr.jaegertracing.io/jaegertracing/jaeger:2.14.0
       ports:
         - "16686:16686"  # Jaeger UI
         - "4317:4317"    # OTLP gRPC
         - "4318:4318"    # OTLP HTTP
       environment:
         - COLLECTOR_OTLP_ENABLED=true
         - LOG_LEVEL=info
         - SPAN_STORAGE_TYPE=memory
       networks:
         - default
   
   networks:
     default:
       driver: bridge
   ```

3. **Start services**:
   ```bash
   docker-compose up -d
   ```

4. **Access Jaeger UI**: **http://localhost:16686**

### Using the Jaeger interface

1. Select service: **trip-backend**
2. Choose operation (optional): e.g., `GET /api/trips`
3. Select time range: e.g., "Last Hour"
4. Click **Find Traces**
5. Click on a trace to view detailed spans and timings

## What gets traced automatically

- ✅ **HTTP Requests**: All FastAPI endpoints with timing and status codes
- ✅ **SQL Queries**: All SQLAlchemy queries with full SQL statements
- ✅ **External HTTP Calls**: HTTPX requests to Google Maps API, OIDC providers, etc.
- ✅ **Complete Timing**: Detailed latency breakdown
- ✅ **Error Traces**: Exceptions with stack traces and context

### Error Response Capture

For all HTTP 4xx/5xx errors, additional attributes are captured:

| Attribute | Description | Example |
|-----------|-------------|---------|
| `http.response.body` | Full error response (max 2KB) | `{"detail":"Not Found"}` |
| `error.message` | Simple error message | `"Not Found"` |
| `error.validation.N.type` | Validation error type | `"missing"`, `"type_error"` |
| `error.validation.N.loc` | Field location | `["body", "username"]` |
| `error.validation.N.msg` | Human-readable message | `"Field required"` |

This makes debugging FastAPI validation errors much easier in Jaeger - you can see the exact error without checking application logs.

Example trace hierarchy:
```
GET /api/trips/123
├─ SELECT FROM trip WHERE id=123
├─ SELECT FROM trip_day WHERE trip_id=123
├─ SELECT FROM trip_item WHERE day_id IN (...)
└─ SELECT FROM place WHERE id IN (...)
```

## Alternatives to Jaeger

### Grafana Tempo

For long-term storage and integration with Grafana dashboards, you can use Tempo instead of Jaeger. Simply configure:

```yaml
OTEL_EXPORTER_OTLP_ENDPOINT: "http://tempo:4317"
```

### Console Exporter (Debug)

To test without an external service, enable console output:

```yaml
OTEL_ENABLED: true
OTEL_CONSOLE_EXPORTER: true
```

Traces will appear in the logs:

```bash
# With Docker
docker-compose logs -f app

# Local development
cd backend
fastapi dev trip/main.py
```

## Additional resources

- [OpenTelemetry Python Documentation](https://opentelemetry.io/docs/languages/python/)
- [FastAPI Instrumentation Guide](https://opentelemetry-python-contrib.readthedocs.io/en/latest/instrumentation/fastapi/fastapi.html)
- [Jaeger Documentation](https://www.jaegertracing.io/docs/)
