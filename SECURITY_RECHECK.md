# INDEPENDENT SECURITY RECHECK & AUDIT

**Target**: TG Power Suite  
**Date**: November 15, 2026  
**Auditor**: Independent Security Engineer & Penetration Reviewer  

---

## 1. Authentication Perimeter Security (SEC-01)

### Attack Vector Analysis: Unauthenticated Remote Streaming
- **Baseline Vulnerability**: In `backend/app/main.py#L456`, an early return permitted any `GET`/`HEAD` request starting with `/dl/` or `/stream/` to bypass `must_auth` if `API_AUTH_TOKEN` was unset.
- **Code Audit**:
  - `backend/app/main.py#L481`:
    ```python
    if is_loopback and not token and not enable_auth and method in ("GET", "HEAD") and path.startswith(("/dl/", "/stream/")):
        await self.app(scope, receive, send)
        return
    ```
  - `backend/app/auth.py#L41`:
    ```python
    if not token_to_check and not ENABLE_API_AUTH and is_loopback and path.startswith(("/dl/", "/stream/")):
        return True
    ```
- **Adversarial Test Results**:
  1. Direct remote request (e.g. `client.host != 127.0.0.1`): `is_loopback` evaluated to `False`; request gated by `must_auth`.
  2. Spoofed headers (`X-Forwarded-For: 203.0.113.10`, `CF-Connecting-IP: 198.51.100.5`, `X-Real-IP: 198.51.100.5`): Immediately identified as forwarded; returned `HTTP 403 Forbidden` (`{"detail":"Forbidden: Non-loopback or remote requests require API_AUTH_TOKEN to be configured on the server."}`).
  3. Scoped Capability Tokens (`playback_grants`):
     - Valid token matching chat and message ID: **Accepted**.
     - Token for different message ID: **Rejected with 403 Forbidden**.
     - Expired or malformed token: **Rejected with 403 Forbidden**.
- **Audit Conclusion**: **SEC-01 is FULLY REMEDIATED and VERIFIED.**

---

## 2. Telegram Session Storage Security (SEC-02 / ISSUE-04)

### Attack Vector Analysis: Session Hijacking via Local Storage
- **Directory and File Permissions**:
  - `backend/sessions/` directory permissions: `drwx------` (`0700`).
  - Session files inside `backend/sessions/`: `-rw-------` (`0600`).
  - **Verdict**: File system access controls on SQLite sessions are secure against other local users.
- **Critical Vulnerability Identified**:
  - In `backend/app/config.py#L147-L160`:
    ```python
    def update_session_string_in_env(session_string: str) -> None:
        global SESSION_STRING
        SESSION_STRING = session_string
        os.environ["TG_SESSION_STRING"] = session_string
        save_env_dict({"TG_SESSION_STRING": session_string})
    ```
  - In `backend/app/telegram_client.py#L468, L662, L679`:
    Upon client connection and login, `update_session_string_in_env()` is invoked, persisting `TG_SESSION_STRING` in plaintext into `backend/.env`.
  - **Risk**: Any backup, git leak, or unprivileged inspection of `.env` exposes the Telethon session string, enabling complete Telegram account takeover without 2FA.
- **Audit Conclusion**: **SEC-02 is PARTIALLY REMEDIATED.** Writing session strings into `.env` must be completely removed.

---

## 3. Network Binding (SEC-03)

- **Baseline**: Server bound to `0.0.0.0`, listening on all network interfaces.
- **Remediated State**:
  - `backend/app/config.py#L193`: `HOST = os.getenv("HOST", "127.0.0.1")`.
  - `docker-compose.yml#L9`: `ports: - "127.0.0.1:8088:8088"`.
- **Audit Conclusion**: **SEC-03 is FULLY REMEDIATED and VERIFIED.**

---

## 4. CORS & CSRF Hardening (SEC-04 & SEC-05)

- **CORS**:
  - Web browser requests sending `Origin` evaluate against `CORS_ORIGINS`.
  - Standalone media players (VLC, MPV, ffmpeg) omitting `Origin` receive `Access-Control-Allow-Origin: *`.
- **CSRF**:
  - `backend/app/main.py#L464-L479`:
    State-mutating methods (`POST`, `PUT`, `DELETE`, `PATCH`) validate the `Origin` header against `CORS_ORIGINS` and local loopback.
  - Cross-site POST requests from external origins (e.g. `http://evil.com`) are rejected with `HTTP 403 Forbidden` (`{"detail":"CSRF validation failed: Cross-origin request not permitted."}`).
- **Audit Conclusion**: **SEC-04 & SEC-05 are FULLY REMEDIATED and VERIFIED.**
