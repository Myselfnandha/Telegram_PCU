# MIGRATION & OPERATOR UPGRADE GUIDE

**Project**: TG Power Suite  
**From Version**: Pre-Remediation Baseline  
**To Version**: Post-Remediation Production Hardened (1.0.0)  
**Date**: November 15, 2026  

---

## 1. Overview of Architectural Changes

The remediated release introduces critical security hardening, streaming unification, and frontend responsive constraints. Operators upgrading existing instances should review the following changes.

---

## 2. Breaking Changes & Deprecations

### 1. Default Network Binding changed from 0.0.0.0 to 127.0.0.1 (SEC-03)
- **Previous Behavior**: FastAPI bound to `0.0.0.0`, exposing port 8088 on all public host IP addresses.
- **New Behavior**: Server binds to `127.0.0.1` by default.
- **Migration Action**:
  - If you run the suite on a local desktop or workstation, no action is needed (`http://127.0.0.1:8088` works seamlessly).
  - If you host the suite on a remote headless VPS and access it directly over LAN/WAN without a reverse proxy, explicitly define `HOST=0.0.0.0` in `backend/.env` AND set `API_AUTH_TOKEN`.

### 2. Strict Authentication on Remote Streaming Routes (SEC-01)
- **Previous Behavior**: Remote requests to `/dl/` and `/stream/` were allowed through without authentication if `API_AUTH_TOKEN` was blank.
- **New Behavior**: Remote requests receive `403 Forbidden` unless `API_AUTH_TOKEN` is set and provided, OR a valid media playback token (`?playback_token=...`) is attached.
- **Migration Action**:
  - In `backend/.env`, configure `API_AUTH_TOKEN=your-token-here`.
  - For external players launched from the Web UI (VLC, MPV), the app automatically generates scoped capability tokens.

### 3. Multi-Range HTTP Requests Return 416 (STRM-01)
- **Previous Behavior**: Requests with `Range: bytes=0-100, 200-300` silently dropped the second range and returned only range 1.
- **New Behavior**: Returns `HTTP 416 Range Not Satisfiable` in accordance with RFC 9110.
- **Migration Action**:
  - Configure download managers (FDM, aria2) to use single byte-range segmented downloads rather than multipart byte range requests.

### 4. Streaming Engine Unification (ARCH-01)
- **Previous Behavior**: FFmpeg transmuxing piped through `fast_stream_engine.py`, while normal streams used `stream_engine.py`.
- **New Behavior**: All streaming routes and transmuxing pipes route through `stream_engine.stream_media()`.
- **Migration Action**:
  - No code changes required by operators. Memory usage is reduced and cache collisions are eliminated.

---

## 3. Session Permissions Verification

To ensure session security, execute the following command after upgrading:
```bash
chmod 700 backend/sessions
chmod 600 backend/sessions/*.session 2>/dev/null || true
```
The application also automatically enforces these permissions at startup.
