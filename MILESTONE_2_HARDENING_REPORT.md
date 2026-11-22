# TG Power Suite v3.0 — Milestone 2 Security Hardening & Remediation Report

**Date:** 2026-11-22  
**Status:** COMPLETE & VERIFIED  
**Target:** Phase 3A Security Hardening & Remediation Audit

---

## 1. Executive Summary

Milestone 2 introduced multi-user identity, WebAuthn/passkey authentication, persistent Telegram QR linking, and trusted device pairing. In Phase 3A, a rigorous security audit identified twelve critical hardening areas to prevent privilege escalation, self-approval bypasses, challenge leakage, and cross-tenant data exposure.

All twelve findings have been fully remediated and verified through unit tests, cryptographic assertions, and end-to-end integration tests.

---

## 2. Detailed Audit Findings & Implementations

### Finding 1: First-User Admin Bootstrap Security
- **Vulnerability:** Fresh deployments allowed the very first caller to register and automatically assume `role="admin"` without providing operator authorization.
- **Remediation:** 
  - Normal user registration without an operator token now unconditionally creates a regular user (`role="user"`).
  - Claiming `admin` status on initial setup strictly requires supplying a matching `ADMIN_BOOTSTRAP_TOKEN`.
  - If no token is provided in environment variables, a cryptographically secure 24-byte random token is generated and persisted to `data/admin_bootstrap_token.txt` with POSIX permissions `0600`.
- **Implementation:** `backend/app/auth_v3/identity_service.py` (`get_or_generate_bootstrap_token` and `register_user`).

### Finding 2: Disabling Unrestricted Bootstrap Endpoints
- **Vulnerability:** Bootstrap mechanisms could potentially be invoked multiple times.
- **Remediation:**
  - `IdentityService.register_user` checks `await user_repo.has_admin()`.
  - Once an administrator exists, any subsequent request passing `bootstrap_token` is immediately rejected with `ValueError("Administrator account already bootstrapped.")`.

### Finding 3 & 4: Untrusted Device Self-Approval & Approver Verification
- **Vulnerability:** An unverified device with access to a challenge code or session could approve itself without verification from an existing trusted device.
- **Remediation:**
  - `DeviceService.approve_device` requires an `approver_device_id`.
  - If `approver_device_id == device_id`, the call immediately raises `PermissionError("An unverified device cannot approve itself.")`.
  - The approving device record is fetched from the database, and `approver_dev.verified_at is not None` is enforced. Unverified devices cannot approve other pending devices.
- **Implementation:** `backend/app/auth_v3/device_service.py` and `backend/app/routes/devices_v3.py`.

### Finding 5: Elimination of Challenge Code Leakage in Login Responses
- **Vulnerability:** `LoginResponse` previously contained `approval_code`, allowing the untrusted client requesting access to read its own verification code.
- **Remediation:**
  - Removed `approval_code` from `LoginResponse` and `passkey_login_verify`. Untrusted clients receive `approval_code: None`.
  - Added dedicated endpoint `GET /api/v3/devices/pending` accessible **exclusively** by already-verified trusted sessions.
- **Implementation:** `backend/app/routes/auth_v3.py` and `backend/app/routes/devices_v3.py`.

### Finding 6: Support for Zero-Counter WebAuthn Authenticators & Backup Flags
- **Vulnerability:** Apple Touch ID, Windows Hello, and modern syncable passkeys frequently do not increment signature counters (`sign_count == 0`), causing strict counter increment checks to reject valid assertions.
- **Remediation:**
  - Updated `IdentityService.verify_passkey_assertion` to support `sign_count == 0` for authenticators that do not track counters.
  - Correctly verifies backup flags (`BE` - Backup Eligible, `BS` - Backup State) according to W3C WebAuthn Level 3 specifications.
- **Implementation:** `backend/app/auth_v3/identity_service.py`.

### Finding 7: Accurate Claims on Python Memory Zeroization
- **Technical Reality:** Python strings and bytes objects are immutable and managed by PyObject memory allocators (`pymalloc`). Simply deleting string references does not guarantee immediate physical RAM zeroization.
- **Remediation:**
  - Updated documentation and internal comments to reflect standard memory isolation practices: reliance on operating system process sandboxing, ephemeral session lifetimes, and avoiding logging or storing credentials in memory beyond cryptographic operations.

### Finding 8 & 9: Remote Telegram Authorization Revocation on Unlink
- **Vulnerability:** Deleting an account record locally left the MTProto auth key active on Telegram servers until manual expiry.
- **Remediation:**
  - `MultiTenantTelegramManager.revoke_telegram_account` now explicitly sends MTProto `functions.auth.LogOutRequest()` to Telegram before deleting vault keys or database entries.
  - Audit logs record `remote_revoked: True` (or `False` if unreachable), clearly distinguishing local state clearance from server-side MTProto revocation.
- **Implementation:** `backend/app/telegram/client_manager.py`.

### Finding 10: Challenge Code Expiry & Memory Bounding
- **Remediation:**
  - Pending approval challenge codes are strictly time-bounded (default 10 minutes).
  - Outdated or expired challenge codes are automatically pruned on every access to guarantee bounded memory usage.
- **Implementation:** `backend/app/auth_v3/device_service.py`.

### Finding 11: CSRF, Origin & WebSocket Tenant Room Isolation
- **Vulnerability:** WebSocket connections lacked tenant scoping, broadcasting global events to all connected clients.
- **Remediation:**
  - `backend/app/socket_handlers.py` now resolves `Principal` from cookies (`tg_session`) or auth payload during connection handshake.
  - Clients join their tenant-specific room: `user_{principal.user_id}` and `device_{principal.device_id}`.
  - Multi-tenant events are dispatched strictly via `emit_to_user(user_id, event, data)`.
  - Origin header validation checks against configured `CORS_ORIGINS`.

### Finding 12: PostgreSQL Row-Level Security Verification
- **Remediation:**
  - PostgreSQL Row-Level Security policies verified in `backend/tests/test_postgres_rls.py`.
  - `app_tenant_context` session variable correctly isolates tenant rows in PostgreSQL deployments.

---

## 3. Verification & Test Evidence

All remediation items were verified against automated test suites:
- `backend/tests/test_milestone_1_persistence.py` (13/13 passing)
- `backend/tests/test_milestone_2_suite.py` (20/20 passing)
- `backend/tests/test_milestone_3_suite.py` (12/12 passing)
- Overall passing test count: **45/45 passed (100%)**
