# TG Power Suite v3.0 — Phase 02: Multi-User Application Authentication

**Status:** IMPLEMENTED & VERIFIED  
**Architecture Version:** 3.0.0  
**Security Boundary:** Multi-Tenant Identity & Session Isolation  

---

## 1. Executive Summary

Phase 02 introduces an independent, multi-user identity and authentication layer for TG Power Suite v3.0. Prior to v3.0, TG Power Suite operated under a single-tenant model relying on global application tokens or loopback trust. Phase 02 establishes tenant isolation, allowing multiple users to register, authenticate, and manage isolated workspaces, linked Telegram accounts, and trusted devices without cross-tenant interference.

The implementation combines:
1. **Passkey / WebAuthn FIDO2 Authentication:** Hardware-backed, phishing-resistant public-key authentication via the W3C WebAuthn standard (`webauthn>=3.0.0`).
2. **Hardened Password Authentication:** Secure password hashing using PBKDF2-HMAC-SHA256 with 600,000 iterations, cryptographically random salts, and constant-time equality checks.
3. **Zero-Knowledge Session Storage:** Cryptographically secure 32-byte opaque session tokens issued to clients; the database stores only high-entropy SHA-256 token digests.
4. **Adaptive Rate Limiting / Brute-Force Throttling:** Sliding-window lockout enforcing a 15-minute freeze after 5 consecutive failed attempts per user/IP.
5. **Secure Administrative Bootstrapping:** Hardened bootstrapping that elevates only the first instance owner or callers supplying a cryptographically verified `ADMIN_BOOTSTRAP_TOKEN`.

---

## 2. Cryptographic Architecture

### 2.1 Password Hashing Specification

Passwords are authenticated and stored using PBKDF2-HMAC-SHA256:
- **Algorithm:** PBKDF2 (`hashlib.pbkdf2_hmac`)
- **Digest:** SHA-256
- **Iterations:** 600,000 (compliant with OWASP 2026 password storage guidance)
- **Salt:** 16 bytes of cryptographically secure random entropy (`secrets.token_bytes(16)`)
- **Storage Format:** `pbkdf2_sha256${iterations}${salt_hex}${derived_key_hex}`
- **Verification:** Constant-time comparison using `hmac.compare_digest` to prevent timing side channels.

### 2.2 WebAuthn / Passkeys Implementation

Passkey enrollment and authentication are implemented via `webauthn>=3.0.0`:
- **Relying Party ID (RP ID):** Configurable via `WEBAUTHN_RP_ID` (default: `localhost`).
- **Relying Party Name:** `TG Power Suite`.
- **Allowed Origins:** Strictly checked against allowed HTTPS / HTTP loopback origins.
- **Challenge Lifecycle:** Server-generated 32-byte random challenges stored in an in-memory challenge registry with strict 300-second TTLs.
- **Credential Storage:** Upon verification, the credential ID, COSE public key bytes, and signature counter (`sign_count`) are persisted in `login_credentials`. Cloned authenticator detection is enforced by rejecting signature counts that fail to advance monotonically.

### 2.3 One-Time Emergency Recovery Codes

Upon account creation, 8 cryptographically unique recovery codes formatted as `XXXX-XXXX` are generated using `secrets.token_hex(3).upper()`. The plaintext codes are returned once to the user, while the database records only their SHA-256 digests in `login_credentials(credential_type="recovery_codes")`.

---

## 3. Session Token Management

### 3.1 Token Entropy & Zero-Knowledge Storage

Session tokens are generated as 32-byte (256-bit) URL-safe random strings:
```python
raw_token = secrets.token_urlsafe(32)
token_hash = hashlib.sha256(raw_token.encode("utf-8")).digest()
```
- **Plaintext Tokens:** Transmitted solely in HTTP responses to the authenticated client (via `Authorization: Bearer <token>` or HTTP-only cookies).
- **Database Storage:** `app_sessions.token_hash` stores only the 32-byte binary SHA-256 digest (`LargeBinary`). Even full database compromise yields zero usable session tokens.
- **Lookup:** Incoming request tokens are hashed with SHA-256 before performing constant-time binary index lookups in `app_sessions`.

### 3.2 Session Lifetime & Cookie Properties

- **Default TTL:** 168 hours (7 days), configurable via `APP_SESSION_TTL_HOURS`.
- **Cookie Security:**
  - Name: `tg_session`
  - `HttpOnly`: Enforced (prevents XSS access).
  - `Secure`: Enforced in production (`ENVIRONMENT=production`).
  - `SameSite`: `Lax` (protects against CSRF on state-changing requests).
  - `Path`: `/`

### 3.3 Revocation & Rotation

- **Single Session Revocation:** Sets `revoked_at = utc_now()`.
- **Global User Revocation:** Revokes all active sessions for a user upon security events or explicit user request.
- **Device Revocation Cascading:** Revoking a trusted device automatically marks all `app_sessions` bound to that `device_id` as revoked in the same transaction.

---

## 4. Administrative Bootstrapping

To prevent unauthenticated callers from claiming administrative control:
1. **Initial Setup Bootstrap:** If and only if the `app_users` table contains zero users, the very first registering user is assigned `role="admin"`.
2. **Token-Gated Bootstrap:** For subsequent admin provisioning, callers must supply a valid `bootstrap_token` matching the externally injected environment variable `ADMIN_BOOTSTRAP_TOKEN` verified via `hmac.compare_digest`.
3. Unauthenticated or non-admin callers cannot self-promote.

---

## 5. API Endpoints (`/api/v3/auth/*`)

| Method | Endpoint | Description | Auth Required |
|---|---|---|---|
| `POST` | `/api/v3/auth/register` | Register new user, issue initial session & recovery codes | No |
| `POST` | `/api/v3/auth/login` | Password login, resolve device, issue session token | No |
| `POST` | `/api/v3/auth/logout` | Revoke current session, clear cookie | Yes |
| `GET` | `/api/v3/auth/me` | Retrieve authenticated user profile | Yes |
| `POST` | `/api/v3/auth/passkey/register-options` | Generate WebAuthn creation options | Yes |
| `POST` | `/api/v3/auth/passkey/register-verify` | Verify and enroll WebAuthn credential | Yes |
| `POST` | `/api/v3/auth/passkey/login-options` | Generate WebAuthn assertion options | No |
| `POST` | `/api/v3/auth/passkey/login-verify` | Verify WebAuthn assertion, issue session | No |

---

## 6. Verification & Automated Test Coverage

Phase 02 is validated by tests in `backend/tests/test_milestone_2_suite.py`:
- `test_user_registration_password_pbkdf2`
- `test_user_login_session_token_issuance`
- `test_session_token_hashing_in_db`
- `test_tenant_isolation_user_a_cannot_access_user_b`
- `test_rate_limiting_login_throttling`
- `test_admin_bootstrap_security`
- `test_session_revocation_single`
- `test_revoke_all_user_sessions`

All 8 authentication tests pass cleanly with 100% verification.
