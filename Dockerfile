# CloudOps Monitor as one container: the API, the monitoring worker and the built Angular app in one
# process.
#
#   docker build -t cloudops-monitor .
#   docker run --rm -p 127.0.0.1:8300:8300 -v cloudops-data:/data \
#       -e CLOUDOPS_OPERATOR_TOKEN=<from cloudops generate-token> cloudops-monitor
#
# The database lives in the /data volume, never in the image. No secrets are baked in: the operator
# token and the optional GitHub token are given at run time. See docs/DEPLOYMENT.md.

# --- 1. Build the frontend (same Node version as development and CI) ----------------------------
FROM node:22.14.0-bookworm-slim AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npx ng build --configuration production

# --- 2. Runtime ---------------------------------------------------------------------------------
FROM python:3.14.9-slim-trixie AS runtime

LABEL org.opencontainers.image.title="CloudOps Monitor" \
      org.opencontainers.image.description="Application health, deployments, logs and events in one place." \
      org.opencontainers.image.source="https://github.com/moeletsimelamu/cloudops-monitor"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY backend/pyproject.toml backend/constraints.txt ./
COPY backend/src ./src
# Exact versions from constraints.txt, installed as a regular (non-editable) package owned by
# root, so the server cannot change its own code.
RUN pip install -c constraints.txt . && rm -rf src build

COPY --from=frontend /build/dist/cloudops-web/browser ./static

# An unprivileged user that owns only the data directory.
RUN useradd --system --uid 10001 --no-create-home cloudops \
    && mkdir /data && chown cloudops:cloudops /data
USER cloudops

ENV CLOUDOPS_DATA_DIR=/data \
    CLOUDOPS_STATIC_DIR=/app/static \
    CLOUDOPS_API_DOCS=false \
    CLOUDOPS_LOG_FORMAT=json

VOLUME ["/data"]
EXPOSE 8300

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8300/api/health/ready', timeout=4)"]

# Inside the container the server listens on all interfaces. Publish the port on the host's
# loopback (-p 127.0.0.1:8300:8300) unless a TLS-terminating proxy is in front of it.
# `cloudops serve` runs uvicorn with the application's own access log and a 15 s graceful
# shutdown; an invalid configuration or unusable database ends it with one clear log line.
CMD ["cloudops", "serve", "--host", "0.0.0.0", "--port", "8300"]
