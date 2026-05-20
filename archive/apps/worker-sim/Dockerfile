# gsfluent v2 sim worker.
#
# Two parts:
#  - This container provides arq + minio + sqlalchemy etc.
#  - The engine (gsfluent.sim, mpm_solver_warp, etc.) is mounted from
#    the host at runtime. That keeps this image small and avoids
#    pinning torch/warp/taichi here.
#
# When the engine is mounted at /opt/engine, PYTHONPATH includes it so
# `import gsfluent.sim` resolves.

FROM python:3.12-slim AS builder

WORKDIR /app

RUN pip install --no-cache-dir 'uv==0.5.*' && \
    apt-get update && \
    apt-get install -y --no-install-recommends build-essential && \
    rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY src/ ./src/

RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir .

# Also install the api package so we can import gsfluent_api.models for
# DB writes. Mounted from sibling apps/api/ via the build context.
# (Compose build context is apps/worker-sim/; api is added as a build arg.)
ARG API_PATH=../api
COPY ${API_PATH}/pyproject.toml /tmp/api/pyproject.toml
COPY ${API_PATH}/src/ /tmp/api/src/
RUN /opt/venv/bin/pip install --no-cache-dir /tmp/api

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
    PYTHONPATH=/app/src:/opt/engine \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

USER gsfluent

CMD ["arq", "worker_sim.jobs.WorkerSettings"]
