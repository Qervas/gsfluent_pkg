# Multi-stage build for the gsfluent v2 api.
# Stage 1 builds the wheel with uv; stage 2 is a slim runtime.

FROM python:3.12-slim AS builder

WORKDIR /app

# uv is a fast pip-compatible installer; matches the user's tooling preference.
RUN pip install --no-cache-dir uv==0.5.* && \
    apt-get update && \
    apt-get install -y --no-install-recommends build-essential && \
    rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY src/ ./src/

# Install into /opt/venv so we can copy a slim runtime layer.
RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir . && \
    /opt/venv/bin/pip install --no-cache-dir uvicorn[standard]

# ---

FROM python:3.12-slim AS runtime

WORKDIR /app

RUN useradd -m -u 1000 gsfluent && \
    apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv
COPY src/ /app/src/

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH=/app/src \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

USER gsfluent
EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --retries=10 \
    CMD curl -fsS http://localhost:8000/v1/system/health || exit 1

CMD ["uvicorn", "gsfluent_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
