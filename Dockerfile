# syntax=docker/dockerfile:1
#
# Multi-stage build producing a single image: the FastAPI backend serves both its own API and
# the pre-built React web client (backend/static), so the whole stack is one deployable unit
# with no separate web server, per the mission's architecture requirements.

# ---- Stage 1: build the web client ----
FROM node:20-slim AS web-builder
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# ---- Stage 2: build the Python backend into a wheel-installed venv ----
FROM python:3.12-slim AS backend-builder
WORKDIR /backend
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
COPY backend/pyproject.toml ./
COPY backend/app ./app
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install .

# ---- Stage 3: final runtime image ----
FROM python:3.12-slim AS runtime

# Non-root user for the whole runtime, per the mission's security requirements.
RUN groupadd --gid 10001 cadence \
    && useradd --uid 10001 --gid cadence --home-dir /app --create-home --shell /usr/sbin/nologin cadence

WORKDIR /app

COPY --from=backend-builder /opt/venv /opt/venv
COPY --from=backend-builder /backend/app /app/app
COPY --from=web-builder /web/dist /app/static

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/app/data \
    MUSIC_LIBRARY_PATH=/app/library

RUN mkdir -p /app/data /app/library \
    && chown -R cadence:cadence /app

USER cadence

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0) if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3).status == 200 else sys.exit(1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
