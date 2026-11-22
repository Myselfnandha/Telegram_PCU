# PRODUCT_CURRENT_STATE.md

## 1. Product Overview
TG Power Suite is a Telegram-centric media streaming and library management platform. It leverages a Rust-based MTProto streaming core for high-performance chunk-based media retrieval and a Python backend for application logic.

## 2. Capability & Traceability Matrix

| Capability | Maturity | Status | Location |
| :--- | :--- | :--- | :--- |
| **Telegram Accounts** | High | Verified | `backend/app/auth.py`, `rust_core/src/session.rs` |
| **Media Discovery** | High | Verified | `backend/app/routes/media.py` |
| **Cinema Experience** | Low | Partially Implemented | `frontend-legacy/js/cinema-ui.js` |
| **Playback** | High | Fully Implemented | `backend/app/routes/proxy_streaming.py` |
| **Preferences** | Mid | Partially Implemented | `backend/app/settings.py` |
| **Security** | Mid | Verified | `backend/app/auth.py`, `rust_core/src/session.rs` |
| **Backup/Restore** | Low | Partially Implemented | `backend/app/services/backup.py` |

## 3. Architecture Summary
- **Frontend**: Split between legacy and new `ReUI` components.
- **Backend**: Python-based API orchestration (FastAPI).
- **Core**: Rust-based MTProto daemon (`rust_core`).
- **Data**: Telemetry counters for chunk-level lifecycle tracking; sessions stored in SQLite files.

## 4. Key Implementation Risks
- **Session Security**: Depends on file system permissions for SQLite session files in `backend/data/`.
- **Large Library Performance**: Performance degrades with >5k items due to frontend main-thread blocking (`JSON.parse` and DOM rendering).
- **Backup/Restore Limitations**: Current system performs targeted cloud sync, not a full system backup.
