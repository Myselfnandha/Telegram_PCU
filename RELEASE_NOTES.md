# TG Power Suite — Release Notes

**Release**: v2.0.0 (Production Release Candidate)  
**Release Date**: November 15, 2026  
**Primary Branch**: `main`  
**Repository**: `TG_TOOL` / `TG_WEB`  
**Target Environments**: Linux (x86_64, aarch64), macOS, Windows, Docker (AMD64/ARM64)

---

## Executive Overview

TG Power Suite v2.0.0 is a complete, production-hardened release of the high-speed Telegram media streaming and management platform. This milestone delivers significant architectural modularization, zero-trust security hardening, full RFC 9110 HTTP Range compliance, container privilege reduction, and a unified streaming engine backed by Rust MTProto acceleration and a modern React 19 frontend.

---

## 1. Major Architectural Improvements

- **Proxy Monolith Decomposition (ISSUE-08)**:
  - Decomposed the 2,098-line monolithic `backend/app/routes/proxy.py` into four domain-focused sub-routers under `backend/app/routes/`:
    - `proxy_streaming.py` (1,217 lines): MTProto streaming, Range parsing, dynamic chunk sizing, transmuxing, prewarming, and buffer management.
    - `proxy_vlc.py` (335 lines): Desktop VLC player launching with loopback validation, batch `.m3u` playlists, and 98% playback tracking.
    - `proxy_thumbnails.py` (378 lines): 7-tier multi-stage thumbnail fallback, web poster discovery, and 5-point hover frame scrubbing.
    - `proxy_subtitles.py` (206 lines): Subtitle extraction, SSA/ASS to WebVTT/SRT conversion, and direct VLC subtitle stream delivery.
  - Refactored `proxy.py` into a clean **102-line aggregation facade** with 100% backward-compatible re-exports for all public functions, classes, and sub-routers.
- **Unified High-Speed Streaming Engine (ISSUE-03)**:
  - Completely retired and unified the duplicate `fast_stream_engine` into a single, high-performance `StreamEngine` pipeline.
  - Dual-anchor lookahead prefetching, adaptive sliding RAM buffer (256 MB cap), and 4KB seek boundary alignment for instant video playback.
- **Centralized Database & Storage Configuration (ISSUE-10)**:
  - Standardized all database and state file paths under `backend/data/` (`history.db`, `cinema_cache.db`, `tg_power_suite.db`).
  - Purged legacy 0-byte SQLite database files from the repository root.

---

## 2. Security Hardening

- **Remote Streaming Capability Tokens (SEC-01 / ISSUE-01)**:
  - Eliminated unauthenticated remote playback access. Remote VLC and web streaming endpoints now require cryptographically signed capability tokens managed by `PlaybackGrantStore` with a 4-hour TTL.
  - Desktop playback launcher (`/api/media/vlc/launch`) strictly enforces loopback peer origins (`127.0.0.1`, `::1`) and rejects reverse-proxy headers (`X-Forwarded-For`, `CF-Connecting-IP`).
- **Elimination of Plaintext Session Storage (SEC-02 / ISSUE-04)**:
  - Permanently blocked `TG_SESSION_STRING` writes to `backend/.env`.
  - Migrated user authorization keys into **protected binary SQLite session databases** in `backend/sessions/` with strict POSIX permissions (`0700` directory, `0600` file permissions) for both primary canonical account (`tg_suite_user_session.session`, with `tg_uploader.session` supported as legacy alias) and secondary account (`tg_suite_secondary_session.session`).
  - Added automated permission guard `TelegramClientManager._secure_session_permissions()` verifying `0700`/`0600` on every client connection.
- **Loopback Default Binding (SEC-03)**:
  - The backend server now defaults to binding on loopback interface `127.0.0.1:8088`. Binding to external interfaces requires explicit configuration via `HOST=0.0.0.0` or tunnel configuration.
- **Restricted CORS & CSRF Controls (SEC-04 / SEC-05)**:
  - Tightened CORS headers to reject arbitrary origins. Added strict Origin validation for state-changing POST/DELETE endpoints and Socket.IO connections.

---

## 3. Streaming & Protocol Enhancements

- **RFC 9110 Section 14.2 HTTP Multi-Range Compliance (ISSUE-12)**:
  - Upgraded `parse_http_range` to follow the official HTTP/1.1 specification.
  - Valid multi-range requests (e.g., `bytes=0-100, 200-300`) return the full representation with `200 OK` when `multipart/byteranges` is unsupported, preventing media players and download managers from stalling.
  - `416 Range Not Satisfiable` is strictly reserved for unsatisfiable ranges (`start >= file_size`) or 0-byte representations.
- **7-Tier Multi-Stage Thumbnail Pipeline**:
  - Implemented multi-tier thumbnail resolution: Disk Cache → Embedded Telegram `PhotoSize` → Adjacent Message Photo → Cinemeta Web Poster API → FFmpeg Video Keyframe → Stripped Photo → Default SVG Placeholder.
- **Canonical 98.0% Playback Completion Threshold**:
  - Standardized the playback completion auto-purge threshold to canonical **98.0%** across `proxy_vlc.py`, `playback_tracker.py`, and all test suites. Videos watched to 98% or beyond are automatically pruned from "Continue Watching".

---

## 4. Frontend & UI/UX Modernization

- **Canonical React 19 Frontend**:
  - Solidified `frontend-reui/` (Vite 8 + React 19 + ReUI / Tailwind) as the sole canonical frontend application.
  - Safely untracked legacy files (`frontend-legacy/`) from Git and added them to `.gitignore` (ISSUE-09).
- **Docked Channel Picker Layout Fix (ISSUE-05)**:
  - Corrected channel dock CSS width constraints to proportional bounds (`24%`, min 280px, max 360px), eliminating layout overflows across 1024px–1920px viewports.
- **Clean Component Exports (ISSUE-11)**:
  - Extracted `badge-variants.ts` and `button-variants.ts` to eliminate React Fast Refresh export warnings in development.

---

## 5. Docker & Container Hardening (ISSUE-06)

- **Unprivileged Non-Root Execution**:
  - The container executes strictly as dedicated user `nonroot:nonroot` (`UID:GID 10001:10001`).
- **Context Minimization**:
  - Introduced root `.dockerignore` cutting build context transfer from **251.87 MB** down to **87.78 KB** (>99.9% optimization).
- **Modernized Multi-Stage Build**:
  - Updated Rust builder stage to `rust:slim` (`rustc 1.100+`) for full compatibility with modern crates requiring `edition2024`.
  - Isolated Python runtime dependencies in `/opt/venv` with standard `python:3.12-slim` base image.
- **Verified Storage Persistence**:
  - Verified that SQLite databases (`history.db`) and protected session files survive container restarts 100% intact with POSIX permissions preserved under UID 10001.

---

## 6. Installation & Deployment

### Option A: Native Host Deployment

```bash
# 1. Clone repository and navigate to workspace
git clone https://github.com/your-org/TG_TOOL.git
cd TG_TOOL

# 2. Setup Python virtual environment (Python 3.12 - 3.14)
python3 -m venv venv
source venv/bin/activate
pip install -r backend/requirements.txt

# 3. Build Rust Core Accelerator
cd rust_core
cargo build --release
cd ..

# 4. Build Frontend (React 19 ReUI)
cd frontend-reui
npm install
npm run build
cd ..

# 5. Launch Backend Server
python backend/run.py start --foreground
```

### Option B: Docker Compose Deployment

```bash
# 1. Build and launch container stack
docker compose up -d --build

# 2. Verify container health status
docker ps --filter "name=tg-power-suite"
curl -s http://127.0.0.1:8088/api/system/health
```

---

## 7. Migration Considerations

1. **Legacy String Sessions**:
   - If an existing installation has `TG_SESSION_STRING` in `backend/.env`, the system automatically migrates the authorization keys to canonical `backend/sessions/tg_suite_user_session.session` (or `tg_uploader.session` if configured) on first launch and purges the plaintext string from `.env`. Existing authorized session files are never overwritten.
2. **Database Path Consolidation**:
   - SQLite databases are strictly maintained in `backend/data/`. Any existing `.db` files from root are ignored and can be safely deleted.
3. **Docker Host Permissions**:
   - When mounting host directories (`backend/data`, `backend/sessions`) into the hardened Docker container, ensure the host directory is accessible by container UID `10001` or use Docker named volumes.

---

## 8. Known Limitations

1. **Rust Accelerator Standby in Unauthenticated Environments**:
   - The Rust MTProto accelerator starts its local daemon on `127.0.0.1:8099`. When running without an authorized Telegram account, it reports `active_accounts: 0`. Full Rust acceleration activates automatically once the primary account logs in.
2. **FFmpeg System Dependency**:
   - Real-time MP4 transmuxing (`/stream/{chat_id}/{message_id}`) and subtitle track extraction require `ffmpeg` and `ffprobe` to be installed on the host system (included by default in the Docker container image).
3. **Single Active VLC Loopback Instance**:
   - Desktop VLC player launching (`/api/media/vlc/launch`) is designed for local desktop sessions and requires an active display server (`DISPLAY` on X11/Wayland).
