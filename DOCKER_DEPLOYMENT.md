# PRODUCTION DOCKER DEPLOYMENT GUIDE

**Application**: TG Power Suite  
**Containerization Architecture**: Multi-Stage Hybrid Build (Node.js 22 + Rust 1.84 + Python 3.12)  
**Date**: November 15, 2026  

---

## 1. Multi-Stage Build Overview

The production `Dockerfile` uses an isolated 4-stage build pipeline to generate a lightweight, secure runtime container containing all Python, Rust, and React assets:

1. **Stage 1 (`frontend-builder`)**:
   - Base: `node:22-alpine`
   - Builds `frontend-reui` with `npm ci` and `npm run build`.
   - Produces static client assets in `dist/`.
2. **Stage 2 (`rust-builder`)**:
   - Base: `rust:1.84-slim`
   - Compiles `rust_core` using Cargo release profile with dependency caching.
   - Produces native accelerator binary `tg-rust-core`.
3. **Stage 3 (`python-builder`)**:
   - Base: `python:3.12-slim`
   - Installs pip packages into `/root/.local`.
4. **Stage 4 (`runtime`)**:
   - Base: `python:3.12-slim`
   - Installs `ffmpeg`, `libmagic1`, and `curl` for container healthchecks.
   - Copies `/usr/local/bin/tg-rust-core` from Stage 2.
   - Copies Python wheels from Stage 3 and static frontend from Stage 1.
   - Restricts session directory permissions (`chmod 700 /app/backend/sessions`).

---

## 2. Quick Deployment with Docker Compose

### Prerequisites
- Docker Engine 24.0+
- Docker Compose v2.20+

### Step 1: Configure Environment Variables
Create or verify `backend/.env`:
```ini
TG_API_ID=1234567
TG_API_HASH=0123456789abcdef0123456789abcdef
TG_PHONE=+1234567890
API_AUTH_TOKEN=your-secure-random-token-here
ENABLE_API_AUTH=true
HOST=0.0.0.0
PORT=8088
```

### Step 2: Launch Containers
Run:
```bash
docker compose up -d --build
```

### Step 3: Verify Container Health
Check running container:
```bash
docker compose ps
```
Output:
```
NAME              IMAGE             COMMAND                  SERVICE          CREATED         STATUS                   PORTS
tg-power-suite    tg-power-suite    "python backend/run.…"   tg-power-suite   1 minute ago    Up 1 minute (healthy)    127.0.0.1:8088->8088/tcp
```

---

## 3. Persistent Volumes

The following host directories are mounted for persistence:
- `./backend/data`: SQLite databases (`history.db`, `tg_power_suite.db`), media metadata, and log files.
- `./backend/sessions`: Telegram SQLite `.session` files (stored with POSIX 0600 permissions).
- `./backend/downloads`: Saved media files and background downloads.
- `./backend/.env`: Configuration secrets and API credentials.

---

## 4. Security Hardening Checklist for Production

1. **Host Network Binding**:
   - Port 8088 is mapped to `127.0.0.1:8088:8088`. Do not bind to `0.0.0.0:8088` without a reverse proxy (e.g. Nginx, Caddy, Traefik) providing TLS termination and WAF filtering.
2. **Authentication Token**:
   - Always set `API_AUTH_TOKEN` in production so that remote/tunnel connections cannot access streaming endpoints without credentials.
3. **Container Healthcheck**:
   - Automatic 30-second healthcheck probes `/api/system/health`.
