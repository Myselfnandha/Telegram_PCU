# TG Power Suite v3.0 — End-to-End Streaming Trace

**Protocol:** HTTP/1.1 & HTTP/2  
**Media Streaming Pipeline:** Multi-Tenant Gateway $\to$ UserTurboPool $\to$ MTProto Slicing $\to$ Client Response  
**Verification Target:** `backend/tests/test_e2e_streaming_integration.py`  

---

## 1. Request Lifecycle Diagram

```mermaid
sequenceDiagram
    autonumber
    actor User as User Agent / VLC Player
    participant Gateway as FastAPI Application Gateway
    participant Auth as GlobalAuth & SessionService
    participant Route as ProxyStreamingRouter (/dl/{chat_id}/{msg_id})
    participant Manager as MultiTenantTelegramManager
    participant Turbo as UserTurboPool & AdaptiveScheduler
    participant Telethon as Telethon MTProto Client(s)
    participant Cache as StreamCacheManager (Disk LRU)

    User->>Gateway: GET /dl/chat_alpha/555 HTTP/1.1 (Range: bytes=1048576-2097151)
    Note over User,Gateway: Authorization: Bearer <opaque_token>
    Gateway->>Auth: GlobalAuthEnforcementMiddleware(request)
    Auth->>Auth: Check Loopback & Verify Bearer Token
    Auth->>Route: Yield Principal(user_id, role, device_id)
    Route->>Manager: get_client(principal, account_id=None)
    Manager->>Manager: Query DB for Primary Linked Account
    Manager-->>Route: Managed Primary Client
    Route->>Turbo: get_pool(user_id)
    Turbo->>Turbo: Select Eligible Workers (Primary + Turbo Secondaries)
    Route->>Cache: check_slice(chat_id, msg_id, 1048576, 1048576)
    alt Cache Miss
        Turbo->>Telethon: Fetch MTProto Part(dc_id, location, offset=1048576, limit=1048576)
        Telethon-->>Turbo: Byte Stream (512KB chunks)
        Turbo->>Cache: put_slice(tenant_prefix, msg_id, 1048576, data)
    else Cache Hit
        Cache-->>Route: Return cached slice from disk (<1ms)
    end
    Route-->>User: HTTP/1.1 206 Partial Content (Content-Range: bytes 1048576-2097151/5242880)
```

---

## 2. Step-by-Step Architectural Trace

### Step 1 — Application Gateway Authentication
Every incoming request to `/dl/{chat_id}/{message_id}` or `/stream/{chat_id}/{message_id}` passes through `GlobalAuthEnforcementMiddleware`:
1. `is_loopback_request(request)` evaluates client IP. If `MULTI_TENANT_STREAMING_ENABLED=True`, loopback bypass is strictly disabled.
2. The `Authorization: Bearer <token>` or session cookie is extracted and SHA-256 hashed.
3. The database is queried for an active `AppSession`.
4. If expired or revoked, HTTP 401 is returned immediately.
5. A validated `Principal(user_id, role, device_id)` object is bound to `request.state.principal`.

### Step 2 — Telegram Account Scope Resolution
1. `MultiTenantTelegramManager.get_client(principal)` resolves the tenant's primary linked Telegram account from `telegram_accounts`.
2. The account state is verified (`status == 'linked'`). If `status == 'relink_required'`, a `RelinkRequiredError` is raised, resulting in HTTP 401.
3. The AES-256-GCM encrypted session string is retrieved from `telegram_session_vault`, decrypted with the master key, and loaded into Telethon.

### Step 3 — Media Accessibility & Multi-Turbo Scheduling
1. `UserTurboPool` inspects active secondary accounts owned by the tenant.
2. For private channels or chats, each secondary account's media access is verified with `GetFileRequest`.
3. Eligible accounts are registered as `AccountWorker` instances in the pool.
4. An EWMA latency tracker dynamically assigns MTProto chunk offsets (512 KB slices) across active workers.

### Step 4 — Slicing & Disk Cache
1. Slices are checked in `StreamCacheManager`.
2. Slices are stored in discrete 10 MB files under `/var/tmp/tg_cache/blocks/` with path sanitization preventing directory traversal.
3. Eviction runs asynchronously if disk space falls below `MIN_FREE_DISK_BYTES` (2 GB).

### Step 5 — HTTP Range Delivery
1. The response emits standard RFC 9110 compliant headers:
   - `HTTP/1.1 206 Partial Content`
   - `Accept-Ranges: bytes`
   - `Content-Range: bytes {start}-{end}/{total}`
   - `Content-Length: {slice_length}`
   - `Content-Type: video/mp4`
   - `ETag: "{hash}"`
