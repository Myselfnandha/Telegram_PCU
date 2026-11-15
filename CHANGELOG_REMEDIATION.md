# CHANGELOG — COMPLETE REMEDIATION & PRODUCTION HARDENING

All notable changes, bug fixes, security remediations, and architectural refactorings executed during the TG Power Suite master remediation process are documented in this file.

---

## [Production Remediation 1.0.0] — 2026-11-15

### 🔒 Security Remediations (P0 & P2)
- **Resolved Remote Media Streaming Authentication Bypass (SEC-01 / ISSUE-01)**:
  - Modified `backend/app/auth.py` and `backend/app/main.py` (`GlobalAuthEnforcementMiddleware`).
  - Restricted unauthenticated `/dl/` and `/stream/` access strictly to verified loopback requests (`127.0.0.1`, `::1`, `localhost`, `testclient`).
  - Mandated valid `API_AUTH_TOKEN` or scoped cryptographic playback grants for all external, LAN, reverse proxy, and Cloudflare tunnel requests.
- **Enforced Safe Network Bindings (SEC-03)**:
  - Changed default server bind host in `backend/app/config.py` from `0.0.0.0` to `127.0.0.1`, preventing accidental network-wide exposure without explicit operator intent.
- **Hardened Telegram Session Storage & File Permissions (SEC-02 / ISSUE-04)**:
  - Enforced POSIX directory permissions `0700` (`drwx------`) on `backend/sessions/`.
  - Enforced file permissions `0600` (`-rw-------`) on all SQLite `.session` files.
- **CORS & CSRF Hardening (SEC-04, SEC-05)**:
  - Enhanced CORS handling in `backend/app/routes/proxy.py` to selectively echo authorized origins with credentials for web clients while maintaining wildcard streaming for standalone players (VLC, MPV).
  - Implemented strict Origin/Referer cross-site request forgery validation in `GlobalAuthEnforcementMiddleware` for state-mutating HTTP methods (`POST`, `PUT`, `DELETE`, `PATCH`).

### 🧪 Test Regression Resolutions (P1)
- **Fixed Playback Resume Auto-Purge Regressions (TEST-01 / ISSUE-02)**:
  - Corrected premature history purge in `backend/app/services/playback_tracker.py`.
  - Re-anchored the completion auto-purge threshold to the verified 98.0% specification, restoring passing status to `test_tier2_resume_purge_boundary_retains_97_point_9` and `test_tier4_complete_end_to_end_playback_session_lifecycle`.
- **Restored Docked Channel Picker Proportional Layout (UI-01 / ISSUE-05)**:
  - Reverted oversized dock classes in `frontend-reui/src/components/DockedChannelPicker.tsx` back to `lg:w-[18%] lg:min-w-[190px] lg:max-w-[260px] w-[18%] min-w-[190px] max-w-[260px]`.
  - Fixed horizontal document/body overflow and element clipping on 1024px tablet and ultra-wide desktop viewports.
- **Resolved Card Hover Overlay Hit-Test Occlusion (UI-02)**:
  - In `frontend-reui/src/views/CinemaView.tsx`, ensured the card hover overlay includes `pointer-events-none` and `z-20` so selection checkboxes with `pointer-events-auto` and `z-40` remain directly clickable and hit-testable.

### 🏗️ Architecture & Streaming Unification (ARCH-01, ARCH-02)
- **Unified Streaming Engines (ISSUE-03)**:
  - Refactored FFmpeg transmuxing stdin feeder in `backend/app/routes/proxy.py` to consume directly from `stream_engine.stream_media()`.
  - Eliminated duplicate buffer allocation, sender borrowing contention, and cache divergence between `fast_stream_engine.py` and `stream_engine.py`.
  - Added graceful asynchronous `shutdown()` method to `StreamEngine` and wired it into `main.py` application lifecycle.
- **RFC 9110 Compliant HTTP Range Handling (STRM-01 / ISSUE-12)**:
  - Updated `parse_http_range` in `backend/app/routes/proxy.py` to explicitly return `HTTP 416 Range Not Satisfiable` for multipart range requests (`bytes=0-100, 200-300`), eliminating silent stream truncation.

### 📦 Deployment & Containerization (BUILD-01 / ISSUE-06)
- **Multi-Stage Production Dockerfile**:
  - Implemented 4-stage build in `Dockerfile`:
    - Stage 1: `node:22-alpine` compiles `frontend-reui` with Vite 8.
    - Stage 2: `rust:1.84-slim` compiles `tg-rust-core` with Tokio and Grammers.
    - Stage 3: `python:3.12-slim` installs Python wheels and dependencies.
    - Stage 4: Production runtime bundles Python environment, FFmpeg, Rust binary at `/usr/local/bin/tg-rust-core`, and React distribution.
- **Hardened Docker Compose Configuration**:
  - Updated `docker-compose.yml` to bind port 8088 to `127.0.0.1:8088:8088`.

### 🧹 Code Hygiene, Portability & UI Quality (QUAL-01 to QUAL-04)
- **Dynamic Path Resolution in Companion Tool (PORT-01 / ISSUE-07)**:
  - Replaced hardcoded developer file path in `telegram-fdm-proxy/tg_fdm_proxy.py` with dynamic `APP_DIR` and `TG_SESSION_PATH` environment resolution.
- **Vite Fast Refresh React HMR Compliance (QUAL-02 / ISSUE-11)**:
  - Extracted `badgeVariants` to `badge-variants.ts` and `buttonVariants` to `button-variants.ts` in `frontend-reui/src/components/ui/`, eliminating Fast Refresh full-reload warnings.
- **Cleaned Stray Database Files (CLEN-01 / ISSUE-10)**:
  - Removed 0-byte SQLite database files from repository root and enforced strict `DATA_DIR` anchoring.
- **Repository Documentation Revamp (DOC-01 / ISSUE-13)**:
  - Rewrote `README.md` to comprehensively document the hybrid Python/Rust microservice, 2-tier cache hierarchy, React 19 UI, and security controls.
