# TG Power Suite v3.0 — Phase 06: Multi-Tenant Telegram Client Manager

**Status:** IMPLEMENTED & VERIFIED  
**Architecture Version:** 3.0.0  
**Security Boundary:** Tenant-Scoped Telegram Connection Pool & Resource Management  

---

## 1. Executive Summary

Phase 06 replaces the legacy singleton client architecture (`TelegramClientManager`) with the multi-tenant `MultiTenantTelegramManager`. In prior versions, TG Power Suite maintained static global Telegram sessions that assumed a single operating identity. 

`MultiTenantTelegramManager` introduces:
1. **Principal-Driven Connection Resolution:** Resolves MTProto clients exclusively for authenticated `Principal` entities, strictly verifying account ownership before establishing or reusing connections.
2. **On-Demand Lazy Connection:** MTProto client instances are initialized and connected only when requested by authorized operations, avoiding upfront resource exhaustion during application startup.
3. **Encrypted Vault Deserialization:** Restores sessions directly from `telegram_session_vault` into in-memory `StringSession` instances, decrypting with tenant-bound AES-256-GCM.
4. **Adaptive Idle Connection Sweeping:** Automatically detects and disconnects idle MTProto connections after a configurable timeout (default: 180 seconds), minimizing memory and socket usage.
5. **Multi-Turbo Account Discovery:** Discovers and exposes verified secondary (`turbo`) accounts belonging strictly to the requesting tenant for parallel chunk acceleration without cross-tenant leakage.
6. **Remote Revocation Resilience:** Gracefully catches `AuthKeyUnregisteredError`, `SessionRevokedError`, and `UserDeactivatedError`, disconnects invalid connections, and marks account status as `relink_required`.

---

## 2. Architecture & Data Flow

```
                      ┌──────────────────────┐
                      │ Authenticated Request│
                      └──────────┬───────────┘
                                 │
                                 ▼
                     Principal(user_id, role)
                                 │
                                 ▼
           ┌───────────────────────────────────────────┐
           │ MultiTenantTelegramManager.get_client()   │
           └─────────────────────┬─────────────────────┘
                                 │
                 Verify Tenant Ownership in DB
                 account.owner_user_id == principal.user_id
                                 │
            ┌────────────────────┴────────────────────┐
            │ Active in Connection Pool?              │
            ├────────────────────┬────────────────────┤
            │ YES                │ NO                 │
            ▼                    ▼                    │
     Return Cached Client  Load & Decrypt Vault       │
     Update last_accessed  Initialize TelegramClient  │
                           Connect & Verify Auth      │
                                 │                    │
                                 ▼                    │
                           Cache in Pool              │
                                 │                    │
                                 ▼                    │
                           Return Client              │
```

---

## 3. Core Capabilities

### 3.1 Principal-Scoped Resolution

The `Principal` dataclass carries the tenant's security context:
```python
@dataclass
class Principal:
    user_id: uuid.UUID
    role: str
    display_name: Optional[str] = None
    device_id: Optional[uuid.UUID] = None
    session_id: Optional[uuid.UUID] = None
```
When `get_client(principal, account_id=None)` is called:
- If `account_id` is omitted, the tenant's primary account is loaded.
- Tenant isolation is strictly enforced: `tg_repo.get_scoped(account_id, principal.user_id)`.
- If an account belongs to another tenant or does not exist, `AccountNotLinkedError` is raised.

### 3.2 Idle Disconnection & Resource Reaping

The client manager runs a background sweeper task every 30 seconds:
```python
async def disconnect_idle_clients(self) -> int:
    now = time.time()
    for account_id, managed in list(self._active_clients.items()):
        if now - managed.last_accessed_at > self.idle_timeout_seconds:
            await managed.client.disconnect()
            self._active_clients.pop(account_id, None)
```
This guarantees that inactive users do not consume MTProto connection slots or socket file descriptors.

### 3.3 Multi-Turbo Account Isolation

For high-speed streaming acceleration, tenants can link additional Telegram accounts (`role="turbo"`). When requesting available secondary accounts:
```python
accounts = await multi_tenant_manager.get_eligible_turbo_accounts(principal)
```
This queries only accounts where `owner_user_id == principal.user_id`, preventing any tenant from utilizing another user's Telegram credentials for Multi-Turbo transfers.

### 3.4 Remote Session Revocation & Relink Flow

If a user revokes the application session from their Telegram mobile app (Settings → Devices → Terminate Session), Telethon raises `AuthKeyUnregisteredError` upon the next wire interaction.

`MultiTenantTelegramManager`:
1. Traps `AuthKeyUnregisteredError`, `SessionRevokedError`, and `UserDeactivatedError`.
2. Marks `telegram_accounts.status = 'relink_required'`.
3. Disconnects and purges the invalid cached client from `_active_clients`.
4. Emits `telegram.auth_revoked` audit event.
5. Raises `RelinkRequiredError`, alerting the frontend to display the relink prompt.

### 3.5 Clean Unlinking & Vault Purging

When an account is unlinked via `revoke_telegram_account(principal, account_id)`:
1. Active MTProto client is disconnected and purged from cache.
2. Encrypted session entry is deleted from `telegram_session_vault`.
3. Account record is removed from `telegram_accounts`.
4. `telegram.unlinked` audit log is recorded.

---

## 4. Verification & Automated Test Coverage

Phase 06 is validated by tests in `backend/tests/test_milestone_2_suite.py`:
- `test_multi_tenant_client_manager_lazy_and_isolation`
- `test_idle_disconnect_and_relink_required_transition`

Both tests pass with 100% verification.
