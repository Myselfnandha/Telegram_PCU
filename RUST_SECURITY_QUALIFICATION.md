# TG Power Suite v3.0 — Rust Security Qualification Report

**Component:** `rust_core` (Grammers MTProto Acceleration Engine)  
**Safety Status:** ISOLATED & DISABLED FOR MULTI-TENANT STREAMING  
**Effective Configuration:** `RUST_MULTI_TENANT_ENABLED=false`  

---

## 1. Rust Acceleration Audit

The `rust_core` binary provides high-throughput Grammers-based chunk fetching for single-tenant operations. During the v3.0 multi-tenant migration, the Rust engine boundary was subjected to rigorous safety and concurrency audits.

```mermaid
flowchart LR
    subgraph MultiTenantPythonApp ["Python Application (FastAPI Gateway)"]
        TenantRouter["ProxyStreamingRouter"]
        MultiTenantMgr["MultiTenantTelegramManager"]
        FlagCheck{"RUST_MULTI_TENANT_ENABLED == true?"}
    end

    subgraph RustBoundary ["Rust Core IPC Boundary"]
        RustBridge["RustBridge (IPC / Shared Memory)"]
        RustCore["rust_core Binary (Grammers)"]
    end

    TenantRouter --> FlagCheck
    FlagCheck -->|False (Default)| MultiTenantMgr
    FlagCheck -.->|True (Blocked)| RustBridge
    RustBridge -.-> RustCore
```

---

## 2. Identified Multi-Tenant Hazards in Rust Core

1. **Shared Memory Space:** The existing Rust binary was designed around a single MTProto session per process instance. Running concurrent multi-tenant requests through a single Rust bridge instance risks cross-tenant session string leakage or socket contention.
2. **Lack of Tenant Scoping:** The Rust IPC protocol currently identifies media transfers solely by `(chat_id, message_id, offset, limit)`, lacking a cryptographic `tenant_id` verification token. An attacker could potentially request chunks from a private chat without tenant ownership verification.
3. **Process Sandboxing:** In containerized environments (Render Free Tier), spawning multiple isolated Rust subprocesses for each tenant would quickly breach the 512 MB memory limit.

---

## 3. Security Decision & Safeguards

1. **Default Off:** In `backend/app/config.py`, `RUST_MULTI_TENANT_ENABLED` defaults strictly to `False`.
2. **Fallback to Telethon:** When multi-tenant streaming is active, all requests are routed exclusively through `MultiTenantTelegramManager` and Telethon client instances.
3. **Bridge Health Check:** `rust_bridge.is_healthy()` safely reports `False` when disabled, ensuring clean fallback with zero exceptions.
4. **Qualification Requirement:** Before Rust multi-tenant streaming can be enabled in a future release, `rust_core` must implement:
   - Tenant-scoped memory arenas.
   - IPC token validation on every chunk request.
   - Strict socket connection limits per tenant.
