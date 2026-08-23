# syntax=docker/dockerfile:1
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# curl is used by the container HEALTHCHECK.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Dependency layer (cached until requirements change).
COPY requirements.txt pyproject.toml ./
RUN pip install -r requirements.txt

# Application source.
COPY main.py ./
COPY sentinel/ ./sentinel/
RUN pip install -e . --no-deps

# Drop privileges.
RUN useradd --create-home --uid 10001 sentinel
USER sentinel

EXPOSE 8000

# PORT is injected by several hosts (Render, Railway, Cloud Run); 8000 stays the
# default so compose, Fly, and a plain `docker run` are unchanged.
ENV PORT=8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD curl -fsS "http://localhost:${PORT}/health" || exit 1

CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
