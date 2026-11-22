# TG Power Suite v3.0 — Phase 04: Persistent Telegram QR Linking

**Status:** IMPLEMENTED & VERIFIED  
**Architecture Version:** 3.0.0  
**Security Boundary:** Multi-Tenant Telegram Linking & Encrypted Vault  

---

## 1. Executive Summary

Phase 04 implements a persistent, tenant-isolated Telegram account linking system based on official Telegram MTProto QR authentication (`TelegramClient.qr_login()`). Telegram accounts remain persistently linked across server restarts, container redeployments, and browser closures without ever writing unencrypted `.session` SQLite files to disk.

Key architectural features:
1. **Deterministic State Machine:** Manages linking attempts through explicit states with bounded timeouts and cancellation cleanup.
2. **In-Memory Isolated Client:** Employs Telethon's `StringSession()` in memory for linking; zero filesystem SQLite sessions are generated during QR logins.
3. **Dual-Format QR Generation:** Emits both raw MTProto token URLs (`tg://login?token=...`) and high-resolution base64 PNG data URIs.
4. **Zero-Knowledge 2FA Flow:** Automatically detects `SessionPasswordNeededError`, prompts the user securely, passes the cloud password directly into MTProto memory, and zeroes the password immediately without logging.
5. **Global Identity Uniqueness & Account Quotas:** Strictly prevents the same Telegram user ID from being linked across multiple application tenants. Enforces a maximum of 1 primary account and 3 total accounts per user.
6. **Round-Trip Vault Validation:** Encrypts sessions via AES-256-GCM with tenant AAD binding and decrypts them back before transitioning state to `LINKED`.

---

## 2. Linking State Machine

```
              ┌─────────┐
              │ CREATED │
              └────┬────┘
                   │ start_link_attempt()
                   ▼
             ┌────────────┐
             │ QR_PENDING ├────────────────────────────┐
             └─────┬──────┘                            │
                   │ QR scanned                        │
                   ▼                                   │
      ┌─────────────────────────┐                      │
      │ TELEGRAM_AUTHENTICATING │                      │
      └────┬───────────────┬────┘                      │
           │               │ SessionPasswordNeeded     │ Timeout / Cancel
           │               ▼                           ▼
           │        ┌───────────────┐           ┌──────────────┐
           │        │ 2FA_REQUIRED  │           │   EXPIRED    │
           │        └──────┬────────┘           │      or      │
           │               │ submit_2fa()       │  CANCELLED   │
           │               ▼                    └──────────────┘
           │      ┌─────────────────┐                  ▲
           └─────►│IDENTITY_VERIFIED│                  │
                  └────────┬────────┘                  │
                           │ Verify Uniqueness & Quota │ Uniqueness / Quota Error
                           ▼                           │
                    ┌────────────┐                     │
                    │ PERSISTING ├─────────────────────┘
                    └─────┬──────┘
                          │ Save & Decrypt Round-Trip
                          ▼
                     ┌──────────┐
                     │  LINKED  │
                     └──────────┘
```

### State Definitions

| State | Description |
|---|---|
| `CREATED` | Link attempt initialized; background task allocated. |
| `QR_PENDING` | Telethon `qr_login()` active; QR URL and base64 PNG data URI generated; awaiting scan. |
| `TELEGRAM_AUTHENTICATING` | QR code was scanned in Telegram mobile app; MTProto token exchange in progress. |
| `2FA_REQUIRED` | Telegram cloud password is required (`SessionPasswordNeededError`); awaiting API password submission. |
| `IDENTITY_VERIFIED` | Profile retrieved via `get_me()`; global tenant uniqueness and quota checks passed. |
| `PERSISTING` | Session exported, encrypted via AES-256-GCM + tenant AAD into `telegram_session_vault`, round-trip verified. |
| `LINKED` | Account persisted in `telegram_accounts`; temporary client disconnected cleanly; attempt complete. |
| `EXPIRED` | Linking attempt exceeded timeout (default: 180 seconds). |
| `CANCELLED` | Explicit cancellation requested by the user. |
| `FAILED` | Terminal error occurred (e.g. duplicate Telegram ID, invalid 2FA password, MTProto error). |
| `RELINK_REQUIRED` | Remote Telegram authorization was revoked; account marked for relinking. |

---

## 3. Security & Isolation Controls

### 3.1 Ephemeral 2FA Password Handling

When Two-Step Verification is active:
1. Telethon raises `SessionPasswordNeededError`.
2. State transitions to `2FA_REQUIRED`, and the background task awaits an `asyncio.Event`.
3. The user submits their password via `POST /api/v3/telegram/link-attempts/{attempt_id}/2fa`.
4. The password is fed directly to `client.sign_in(password=password)`.
5. The password string is immediately overwritten: `attempt._2fa_password = None`.
6. Sensitive data logging filters actively redact any password or token occurrences from server and container logs.

### 3.2 Global Uniqueness Across Tenants

When `get_me()` returns the authenticated user ID (`me.id`):
```python
existing = await tg_repo.get_by_telegram_user_id(tg_user_id)
if existing and existing.owner_user_id != attempt.user_id:
    attempt.state = STATE_FAILED
    attempt.error_message = f"Telegram account ({tg_user_id}) is already linked to another user."
    return
```
This guarantees that User B cannot link or hijack a Telegram account already registered to User A.

### 3.3 Account Quotas & Primary Constraint

- **Maximum Accounts:** A user can link at most 3 Telegram accounts total (`count_user_accounts(user_id) < 3`).
- **Primary Account Constraint:** Exactly one primary account is allowed per user. Any additional accounts must be designated as `role="turbo"`.

### 3.4 Encrypted Vault Storage & Round-Trip Gate

Once authenticated:
1. `session_str = client.session.save()`
2. `vault_repo.save_session(user_id, account_id, session_str)`:
   - Random 96-bit nonce
   - AES-256-GCM authenticated ciphertext
   - Associated Data: `AAD = f"tenant:{user_id}|account:{account_id}|ver:1".encode()`
3. Round-trip verification:
   ```python
   decrypted_check = await vault_repo.load_session(user_id, account_id)
   if decrypted_check != session_str:
       raise ValueError("Round-trip vault encryption check failed.")
   ```
4. Only upon successful round-trip verification does the state transition to `LINKED`.

---

## 4. API Endpoints (`/api/v3/telegram/*`)

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/v3/telegram/link-attempts` | Start QR link attempt (role: `primary` or `turbo`) |
| `GET` | `/api/v3/telegram/link-attempts/{id}` | Poll link attempt status, expires_in, and QR data |
| `POST` | `/api/v3/telegram/link-attempts/{id}/2fa` | Submit Telegram cloud password |
| `POST` | `/api/v3/telegram/link-attempts/{id}/cancel` | Cancel active link attempt |
| `GET` | `/api/v3/telegram/accounts` | List linked accounts for authenticated tenant |
| `DELETE` | `/api/v3/telegram/accounts/{id}` | Unlink account and purge encrypted vault data |
| `POST` | `/api/v3/telegram/accounts/{id}/set-primary` | Promote turbo account to primary |

---

## 5. Verification & Automated Test Coverage

Phase 04 is validated by tests in `backend/tests/test_milestone_2_suite.py`:
- `test_qr_linking_state_machine_created_to_qr_pending`
- `test_qr_linking_2fa_challenge_handling`
- `test_duplicate_telegram_id_rejection_across_tenants`
- `test_account_limits_one_primary_max_three_accounts`
- `test_round_trip_vault_encryption_verification`
- `test_qr_linking_cancellation_and_cleanup`

All tests pass with 100% compliance.
