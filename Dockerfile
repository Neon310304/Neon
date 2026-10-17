FROM python:3.11-slim-trixie AS base

RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip uninstall -y pip setuptools wheel

# ---- Stage 1: Build ----
FROM base AS builder

WORKDIR /app

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements-runtime.txt .
RUN python -m pip install --no-cache-dir --upgrade pip==26.2.1 setuptools==84.0.0 wheel==0.48.0 \
    && python -m pip install --no-cache-dir -r requirements-runtime.txt \
    && python -m pip uninstall -y pip wheel

# ---- Stage 2: Production ----
FROM base

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Security: run as non-root user
RUN useradd --create-home --shell /usr/sbin/nologin appuser

# Copy application code
COPY --chown=appuser:appuser . .

# Create data directory with correct ownership
RUN mkdir -p /app/data /app/.runtime \
    && chown -R appuser:appuser /app/data /app/.runtime

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=6s --retries=3 --start-period=60s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/v1/health', timeout=5)" || exit 1

CMD ["python", "-m", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
