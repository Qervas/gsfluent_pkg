# gsfluent v2 render worker — aiortc + viser + NVENC.
# Stage 1 builds the wheel + native deps. Stage 2 is a slim runtime.

FROM python:3.12-slim AS builder

WORKDIR /app

# aiortc + av need a chunky pile of -dev libs to compile.
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential pkg-config \
      libffi-dev libssl-dev libopus-dev libvpx-dev \
      libavformat-dev libavcodec-dev libavdevice-dev libavutil-dev \
      libswscale-dev libswresample-dev libavfilter-dev \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir 'uv==0.5.*'

COPY pyproject.toml ./
COPY src/ ./src/

RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir .

# Pull in the api package so we can import gsfluent_api.event_store / events.
ARG API_PATH=../api
COPY ${API_PATH}/pyproject.toml /tmp/api/pyproject.toml
COPY ${API_PATH}/src/ /tmp/api/src/
RUN /opt/venv/bin/pip install --no-cache-dir /tmp/api

# ---

FROM python:3.12-slim AS runtime

WORKDIR /app

RUN useradd -m -u 1000 gsfluent && \
    apt-get update && apt-get install -y --no-install-recommends \
      libopus0 libvpx7 \
      libavformat-dev libavcodec-dev libavutil-dev libswscale-dev \
      libswresample-dev curl ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv
COPY src/ /app/src/

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH=/app/src \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

USER gsfluent

# Metrics port (Task 5+) — exposed for prometheus.
EXPOSE 8080

CMD ["python", "-m", "worker_render.main"]
