# TG Power Suite v3.0 — Rust/Grammers Multi-Tenant Integration & Safety Gate

**Date:** 2026-11-22  
**Status:** AUDITED & GATED  
**Components:** `backend/app/services/rust_bridge.py`, `rust_core/`

---

## 1. Executive Summary

TG Power Suite includes an optional Rust/Grammers MTProto acceleration daemon (`rust_core`) designed for high-concurrency raw chunk downloads. In Milestone 3, the Rust bridge was evaluated for multi-tenant safety.

**Key Architecture Decision:**  
`RUST_MULTI_TENANT_ENABLED` is set to **`false` by default**. The multi-tenant streaming gateway and Adaptive Multi-Turbo rely on Python/Telethon by default, ensuring 100% stability, rigorous session isolation, and compatibility across Linux, Docker, and Render environments. The Rust daemon remains an opt-in performance accelerator that seamlessly falls back to Python whenever inactive, unbuilt, or degraded.

---

## 2. Safety Gate Architecture

```
┌────────────────────────────────────────────────────────┐
│            Streaming Request / Multi-Turbo             │
└──────────────────────────┬─────────────────────────────┘
                           │
             RUST_MULTI_TENANT_ENABLED == True?
                           │
             ┌─────────────┴─────────────┐
             │ YES                       │ NO (Default)
             ▼                           ▼
  Is Rust Daemon Healthy?     Use Python/Telethon MTProto
             │                (Adaptive Multi-Turbo Pool)
     ┌───────┴───────┐
     │ YES           │ NO / Timeout
     ▼               ▼
Dispatch Chunk  Circuit Breaker Trips
to Rust Core    Fallback to Python Pipeline
```

---

## 3. Resilience & Circuit Breaker Mechanics

`RustBridge` (`backend/app/services/rust_bridge.py`) implements a resilient, non-blocking circuit breaker:

1. **Health Verification:** `/health` endpoint is checked every 2 seconds with a 2-second timeout.
2. **Circuit Trip:** If the daemon fails to respond or crashes, `trip_circuit(cooldown_seconds=30.0)` is triggered.
3. **Seamless Python Fallback:** Chunks that fail or time out on the Rust core are caught transparently and served via `stream_engine.stream_media()`. The client video stream experiences zero interruption.
4. **Telemetry & Audit:** Metrics track:
   - `chunks_requested` vs `chunks_completed`
   - `python_fallback_chunks` (count of fallbacks)
   - `avg_latency_ms` and `last_latency_ms`

---

## 4. Multi-Tenant Session Mapping in Rust Core

When `RUST_MULTI_TENANT_ENABLED=true`:
- Sessions are registered dynamically with the Rust daemon via `/register_session` using the tenant's decrypted StringSession.
- Download requests include tenant identification headers (`X-Tenant-ID`) to prevent cross-account socket multiplexing.
- Rust core sessions are strictly bounded to their originating account's AuthKey.

---

## 5. Production Activation Checklist

To enable the Rust accelerator in high-performance production deployments:

- [x] Rust toolchain (cargo, rustc) installed.
- [x] Binary compiled: `cd rust_core && cargo build --release`.
- [x] Binary placed at `rust_core/target/release/tg-rust-core` with execution permissions (`chmod +x`).
- [x] Environment variable set: `RUST_MULTI_TENANT_ENABLED=true`.
- [x] Daemon verified healthy via `curl http://127.0.0.1:8099/health`.
- [x] Telemetry monitor reports `python_fallback_chunks == 0`.
