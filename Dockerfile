# syntax=docker/dockerfile:1.6
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

# System deps: ffmpeg for audio concat, ca-certs for HTTPS feeds, tzdata for scheduling.
RUN     apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        ca-certificates \
        tzdata \
        fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Create unprivileged user
RUN groupadd --system app && useradd --system --gid app --home /app --shell /usr/sbin/nologin app

WORKDIR /app

# Install Python deps first for layer caching.
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy source.
COPY pyproject.toml /app/pyproject.toml
COPY src /app/src
COPY README.md /app/README.md

# Install the package itself (no deps re-resolution).
RUN pip install --no-cache-dir --no-deps -e /app

# Output dir, owned by non-root user.
RUN mkdir -p /app/out && chown -R app:app /app

USER app

ENV OUTPUT_DIR=/app/out

ENTRYPOINT ["python", "-m", "podcaster_ai.run"]
CMD []
