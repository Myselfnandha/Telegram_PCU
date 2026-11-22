# syntax=docker/dockerfile:1.4
# ==============================================================================
# TG Power Suite — Production Multi-Stage Hardened & Cached Dockerfile
# ==============================================================================

# --- Stage 1: Build Frontend (React 19 + Vite 8) ---
FROM node:22-alpine AS frontend-builder
WORKDIR /app/frontend-reui
COPY frontend-reui/package*.json ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci --prefer-offline --no-audit
COPY frontend-reui/ ./
RUN npm run build

# --- Stage 2: Build Rust Core Accelerator (Tokio + Grammers) ---
FROM rust:slim AS rust-builder
WORKDIR /app/rust_core
RUN apt-get update && apt-get install -y --no-install-recommends pkg-config libssl-dev && rm -rf /var/lib/apt/lists/*
COPY rust_core/Cargo.* ./
# Pre-compile crate dependencies with persistent cargo cache
RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/usr/local/cargo/git \
    --mount=type=cache,target=/app/rust_core/target \
    mkdir src && echo "fn main() {}" > src/main.rs && \
    cargo build --release && rm -rf src
COPY rust_core/src ./src
RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/usr/local/cargo/git \
    --mount=type=cache,target=/app/rust_core/target \
    touch src/main.rs && cargo build --release && \
    cp /app/rust_core/target/release/tg-rust-core /usr/local/bin/tg-rust-core

# --- Stage 3: Python Dependencies Builder ---
FROM python:3.12-slim AS python-builder
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends build-essential curl && rm -rf /var/lib/apt/lists/*
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY backend/requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt

# --- Stage 4: Production Runtime Image ---
FROM python:3.12-slim AS runtime
WORKDIR /app

# Install runtime dependencies (FFmpeg, libmagic, curl for healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ffmpeg \
    libmagic1 \
    && rm -rf /var/lib/apt/lists/*

# Create dedicated non-root user and group
RUN groupadd -r -g 10001 nonroot && \
    useradd -r -u 10001 -g nonroot -d /app -s /sbin/nologin -c "Non-root container user" nonroot

# Copy Python virtual environment from builder
COPY --from=python-builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV HOST=0.0.0.0
ENV PORT=8088
ENV TG_HOST=0.0.0.0
ENV TG_PORT=8088

# Copy Rust core accelerator binary from builder
COPY --from=rust-builder /usr/local/bin/tg-rust-core /usr/local/bin/tg-rust-core
RUN chmod 755 /usr/local/bin/tg-rust-core

# Copy compiled frontend assets
COPY --from=frontend-builder /app/frontend-reui/dist /app/frontend-reui/dist
COPY --from=frontend-builder /app/frontend-reui/dist /app/frontend

# Copy backend application and assets
COPY backend /app/backend
COPY assets /app/assets
COPY alembic.ini /app/alembic.ini

# Create required runtime directories with secure permissions and nonroot ownership
RUN mkdir -p /app/backend/data /app/backend/sessions /app/backend/temp_uploads /app/backend/downloads && \
    chmod 700 /app/backend/sessions && \
    chown -R nonroot:nonroot /app

USER nonroot

EXPOSE 8088

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:${PORT:-8088}/api/system/health || exit 1

CMD ["python", "backend/run.py", "start", "--foreground"]
