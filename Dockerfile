# ==============================================================================
# TG Power Suite — Multi-Stage Production Dockerfile
# ==============================================================================

FROM python:3.12-slim AS builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY backend/requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# Final runtime image
FROM python:3.12-slim

WORKDIR /app

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libmagic1 \
    && rm -rf /var/lib/apt/lists/*

# Copy python packages from builder
COPY --from=builder /root/.local /root/.local
ENV PATH=/root/.local/bin:$PATH
ENV PYTHONUNBUFFERED=1
ENV TG_HOST=0.0.0.0
ENV TG_PORT=8088

# Copy application files
COPY backend /app/backend
COPY frontend /app/frontend
COPY assets /app/assets

# Create required runtime directories
RUN mkdir -p /app/backend/data /app/backend/sessions /app/backend/temp_uploads /app/backend/downloads

WORKDIR /app/backend

EXPOSE 8088

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8088/api/system/stats || exit 1

ENTRYPOINT ["python", "run.py"]
CMD ["start", "--foreground"]
