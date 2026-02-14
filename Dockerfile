# Node builder
FROM node:22 AS build
WORKDIR /app
COPY src/package*.json ./
RUN npm install
COPY src .
RUN npm run build

# Server base (without observability)
FROM python:3.12-slim AS base
LABEL maintainer="github.com/itskovacs"
LABEL description="Minimalist POI Map Tracker and Trip Planner"
WORKDIR /app
COPY backend .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r trip/requirements.txt
COPY --from=build /app/dist/trip/browser ./frontend
EXPOSE 8000
CMD ["fastapi", "run", "/app/trip/main.py", "--host", "0.0.0.0", "--port", "8000"]

# Server with OpenTelemetry (default)
FROM base AS otel
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r trip/requirements-otel.txt

# Default target includes OTEL
FROM otel