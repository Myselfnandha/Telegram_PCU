# TG Power Suite v3.0 — Phase 07: Adaptive Multi-Turbo Architecture & Specifications

**Date:** 2026-11-22  
**Status:** IMPLEMENTED & VERIFIED  
**Component:** `backend/app/telegram/turbo_pool.py` & `backend/app/routes/turbo_v3.py`

---

## 1. Overview & Objectives

In TG Power Suite v2.0.0, Multi-Turbo operated as a global, single-user dual-client setup (`TelegramClientManager._client` and `_secondary_client`). In v3.0.0, Multi-Turbo has been re-architected into an **Adaptive Multi-Tenant Turbo Pool**:

1. **Strict Tenant Isolation:** Each user has an independent, encapsulated worker pool (`UserTurboPool`). Zero cross-user worker sharing or session pooling.
2. **Account Ceiling:** Enforces a maximum of **3 Telegram accounts per user** (1 Primary + up to 2 Secondary/Turbo accounts).
3. **Independent Media Verification & File Reference Binding:** Respects Telegram MTProto cryptography by ensuring every account independently fetches its own `InputFileLocation` and `file_reference`.
4. **Adaptive Scheduling:** Dynamically balances chunk dispatch using Exponentially Weighted Moving Average (EWMA) latency and throughput metrics.
5. **FloodWait & Error Isolation:** Protects transfers from single-account rate limits or permission failures through automatic cooldown and seamless fallback.

---

## 2. Core Architecture

```
┌────────────────────────────────────────────────────────────────────────┐
│                        UserTurboPool (Tenant A)                        │
├────────────────────────────────────────────────────────────────────────┤
│ Mode: Adaptive | Active Workers: 3/3 | Tenant ID: 550e8400-...         │
├──────────────────────┬──────────────────────┬──────────────────────────┤
│ Worker 1 (Primary)   │ Worker 2 (Turbo 1)   │ Worker 3 (Turbo 2)       │
│ DC: 2 | Lat: 42ms    │ DC: 2 | Lat: 55ms    │ DC: 2 | Lat: 120ms       │
│ Active: 1 chunk      │ Active: 1 chunk      │ Active: 0 chunks         │
│ Status: READY        │ Status: READY        │ Status: COOLDOWN (12s)   │
└──────────┬───────────┴──────────┬───────────┴──────────┬───────────────┘
           │                      │                      │
           ▼                      ▼                      ▼
┌────────────────────────────────────────────────────────────────────────┐
│                   Adaptive Chunk Scheduler & Assembly                  │
│                                                                        │
│   Chunk 0 [Worker 1] ───► In-Flight ───┐                               │
│   Chunk 1 [Worker 2] ───► In-Flight ───┼─► Deterministic In-Order Yield │
│   Chunk 2 [Worker 1] ───► In-Flight ───┘   (0 ──► 1 ──► 2 ──► ...)     │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Account Ceiling & Zero Cross-User Pooling

- **Ceiling:** `MAX_ACCOUNTS_PER_USER = 3`
  - 1 Primary Account: Default identity for chat browsing, message sending, and downloads.
  - Up to 2 Turbo Accounts: Auxillary connections used strictly for accelerated parallel media chunks.
- **Isolation Guarantee:**
  - `TurboPoolManager` indexes pools by `user_id`.
  - When User A streams media, chunks are dispatched **only** to Worker accounts owned by User A.
  - Under no circumstances does User A's transfer utilize User B's Telegram session, auth key, or network connection.

---

## 4. MTProto Cryptographic Invariant: File Reference Binding

### The Telegram Security Constraint
Telegram MTProto file references are cryptographically bound to the account auth key and DC that generated them. If Account A queries a message and obtains `file_reference_A`, sending a `GetFileRequest` with `file_reference_A` using Account B's connection will result in `FILE_REFERENCE_INVALID` or `FILEREF_UPGRADE_NEEDED`.

### Solution: Independent Media Verification
Before scheduling chunks:
1. `UserTurboPool.check_media_eligibility(chat_id, message_id)` iterates through all pool workers.
2. Each worker client independently executes `client.get_messages(chat_id, ids=message_id)`.
3. The account-specific `InputFileLocation` and `file_reference` are extracted and stored in `worker.file_location`.
4. If an account lacks access (e.g. `ChannelPrivateError`, `ChatAdminRequiredError`), it is marked `is_eligible = False` and excluded from the transfer.
5. If only the primary account has access, the pool automatically falls back to single-account streaming without disruption.

---

## 5. Scheduler Modes

| Mode | Pipeline Window | Concurrency Strategy | Use Case |
| :--- | :--- | :--- | :--- |
| **`off`** | 1 chunk | Primary account only. | Strict rate conservation; low bandwidth connections. |
| **`balanced`** | 2 chunks / worker (max 4) | Selects worker with lowest active in-flight chunks. | Stable streaming; avoids high socket utilization. |
| **`adaptive`** (Default) | 3 chunks / worker (max 6) | EWMA score: $\text{latency} \times (1 + 0.5 \times \text{in\_flight})$. Fastest accounts get more chunks. | Optimal speed with automatic throttling on congestion. |
| **`maximum`** | 4 chunks / worker (max 8) | Aggressive dispatch across all ready accounts. | High-speed bulk downloads (Free Download Manager / aria2). |

---

## 6. EWMA Metric Tracking & FloodWait Isolation

### Metrics Calculation
For each completed chunk:
$$\text{EWMA}_{\text{latency}} = \alpha \cdot \text{latency}_{\text{sample}} + (1 - \alpha) \cdot \text{EWMA}_{\text{latency}}$$
$$\text{EWMA}_{\text{throughput}} = \alpha \cdot \text{throughput}_{\text{sample}} + (1 - \alpha) \cdot \text{EWMA}_{\text{throughput}}$$
Where $\alpha = 0.3$.

### Failure & FloodWait Handling
1. **FloodWait:** If an account receives `FloodWaitError(seconds=N)`, `worker.floodwait_until = now + N`. The scheduler skips this worker until the cooldown expires.
2. **Chunk Retry:** If worker $X$ fails to fetch chunk $K$, chunk $K$ is immediately reassigned to an available fallback worker (preferring Primary) and re-queued.
3. **Latency Penalty:** On non-fatal network glitches, a worker's EWMA latency is multiplied by 1.5, naturally diverting subsequent chunk requests to faster peers.

---

## 7. Deterministic Chunk Reassembly

Streaming responses and playback clients (such as VLC, Safari, Chrome) require strictly contiguous byte streams. Out-of-order byte delivery corrupts video demuxers.

`UserTurboPool.stream_byte_range`:
1. Divides the requested range into discrete 512KB chunks.
2. Dispatches chunk download tasks asynchronously into an in-flight sliding window.
3. Holds an in-memory queue awaiting chunk index `N`.
4. Yields bytes **strictly in sequential order**: $0 \to 1 \to 2 \to \dots \to N$.
5. On client disconnect, all in-flight download tasks are cancelled immediately to prevent bandwidth wastage.
