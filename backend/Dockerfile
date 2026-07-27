# syntax=docker/dockerfile:1

# ---- Builder: installs Python dependencies (build tools live only here) ----
FROM python:3.12-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# ---- Runtime: slim image, no build toolchain, non-root user ----
FROM python:3.12-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --shell /bin/bash appuser

WORKDIR /app

COPY --from=builder /root/.local /home/appuser/.local
COPY --chown=appuser:appuser . .

RUN chmod +x docker-entrypoint.sh

USER appuser

ENV PATH=/home/appuser/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

# No unauthenticated health-route exists in the app (and adding one is
# outside "deployment files only" scope) - checking for *any* HTTP
# response (not `curl -f`, which would treat the real 401 as failure)
# is enough to prove the process is up and accepting connections.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -s -o /dev/null http://localhost:8000/api/auth/me || exit 1

ENTRYPOINT ["./docker-entrypoint.sh"]
