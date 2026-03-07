FROM python:3.13-slim

LABEL purpose="mutation-testing"
LABEL project="the-sentinel"

WORKDIR /app

# Install system deps (gcc needed for scikit-learn wheel fallback)
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc && \
    rm -rf /var/lib/apt/lists/*

# Install Python deps first (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source and config
COPY pyproject.toml .
COPY main.py .
COPY sentinel/ sentinel/

# Install project so imports resolve correctly
RUN pip install --no-cache-dir -e .

# Verify tests pass before mutating
RUN python -m pytest -x --tb=short -q

# Default: run mutmut against domain/application/infrastructure
ENTRYPOINT ["mutmut"]
CMD ["run"]
