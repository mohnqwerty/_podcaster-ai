# syntax=docker/dockerfile:1.6
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

# System deps: ffmpeg for audio concat, ca-certs for HTTPS feeds, tzdata for scheduling,
# Chromium + deps for Playwright-based web scraping.
RUN     apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        ca-certificates \
        tzdata \
        fonts-dejavu-core \
        chromium \
        chromium-driver \
        libnss3 \
        libnspr4 \
        libatk1.0-0 \
        libatk-bridge2.0-0 \
        libcups2 \
        libdrm2 \
        libdbus-1-3 \
        libxkbcommon0 \
        libxcomposite1 \
        libxdamage1 \
        libxfixes3 \
        libxrandr2 \
        libgbm1 \
        libpango-1.0-0 \
        libcairo2 \
        libasound2 \
    && rm -rf /var/lib/apt/lists/*

# Create unprivileged user
RUN groupadd --system app && useradd --system --gid app --home /app --shell /usr/sbin/nologin app

WORKDIR /app

# Install Python deps first for layer caching.
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt \
    && PLAYWRIGHT_BROWSERS_PATH=0 python -m playwright install chromium 2>&1 | tail -3

# Copy source.
COPY pyproject.toml /app/pyproject.toml
COPY src /app/src
COPY README.md /app/README.md

# Install the package itself (no deps re-resolution).
RUN pip install --no-cache-dir --no-deps -e /app

# Output dir, owned by non-root user.
RUN mkdir -p /app/out && chown -R app:app /app

USER app

ENV OUTPUT_DIR=/app/out \
    PLAYWRIGHT_BROWSERS_PATH=0

ENTRYPOINT ["python", "-m", "podcaster_ai.run"]
CMD []
