# PRODUCT_GAP_ANALYSIS.md

## 1. Missing Capabilities
- **Full System Backup/Restore**: Current backup is a targeted cloud sync. System-level settings, accounts, and full catalog state require explicit mapping and validation for restoration.
- **Chronological Cinema UX**: The grid currently groups media by channel/series rather than a unified chronological timeline as required.

## 2. Incomplete Workflows
- **Account Recovery**: Error handling for session-expiry or auth-failure is minimal and requires user intervention via settings rather than automatic recovery flows.
- **Cinema UX Consistency**: Responsiveness and performance for large libraries (>5k items) is unverified and likely to block on frontend rendering.

## 3. Usability & Performance Bottlenecks
- **Large Library Loading**: 
  - **Backend**: DB query latency (SQLite scanning) and API serialization for large JSON payloads.
  - **Frontend**: `JSON.parse` blocking main thread, heavy DOM rendering (thousands of DOM elements).
  - **Assets**: Sequential thumbnail loading impacting network performance.
- **Empty/Loading States**: Needs visual refinement.

## 4. Risk & Dependency Register

| ID | Capability | Risk | Dependency | Acceptance Criteria |
| :--- | :--- | :--- | :--- | :--- |
| **R-01** | Session Security | Filesystem access in `backend/data/*.db` | FS Permissions | Strict file ownership restricted to app user. |
| **R-02** | Library Performance | Main-thread blocking (JSON/DOM) | None | <1s TTI for 5k items. |
| **R-03** | Cinema Grid | Non-chronological grouping | UI Rewrite | Unified chronological timeline across channels. |
