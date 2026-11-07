# The sandbox test target, for Docker Compose and the CI smoke test. Build from the repository root:
#
#   docker build -f sandbox/Dockerfile -t cloudops-sandbox .
#
# It is a demonstration target, not a product: its failures are switched on purpose through
# PUT /control/mode, which needs SANDBOX_CONTROL_TOKEN when called from outside the container.
FROM python:3.14.9-slim-trixie

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
# The backend's constraints pin the shared dependencies (FastAPI, uvicorn) to the same versions.
COPY backend/constraints.txt ./
COPY sandbox/pyproject.toml ./
COPY sandbox/src ./src
RUN pip install -c constraints.txt . && rm -rf src build

RUN useradd --system --uid 10002 --no-create-home sandbox
USER sandbox

ENV SANDBOX_HOST=0.0.0.0 \
    SANDBOX_PORT=8310
EXPOSE 8310

CMD ["cloudops-sandbox"]
