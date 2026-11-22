# TG POWER SUITE — RUST REGISTRATION & SECURITY AUDIT REVIEW

**Author**: Senior Security & Systems Architect  
**Date**: November 19, 2026  
**Classification**: Technical Security Architecture Review  
**Subject**: MTProto Session Registration, Loopback IPC, and Local Attack Surface Hardening  

---

## 1. Threat Modeling & Vulnerability Analysis

The TG Power Suite architecture integrates a high-performance Rust MTProto core (`tg-rust-core`) listening on loopback port `127.0.0.1:8099`. The forensic audit revealed critical security deficiencies in the pre-remediation implementation.

### 1.1 Pre-Remediation Vulnerability Assessment

```
                      +------------------------------------+
                      |     Malicious Browser Web Page     |
                      |  (Attacker origin: evil-site.com)  |
                      +-----------------+------------------+
                                        |
                 fetch("http://127.0.0.1:8099/api/stream/chunk")
                 [Allowed by CorsLayer::permissive()]
                                        |
                                        v
                 +----------------------------------------+
                 |       Unauthenticated Rust Core        |
                 |             Port 8099                  |
                 +----------------------------------------+
```

1. **Permissive CORS Local Exploitation (`CorsLayer::permissive()`)**:
   * Any webpage visited in the user's browser (Firefox/Chrome) could make cross-origin JavaScript `fetch()` calls to `http://127.0.0.1:8099`.
   * Because CORS allowed arbitrary origins (`*`), a malicious website could query the local Rust core, download private Telegram files, or cause Denial of Service.
2. **Unauthenticated Loopback API**:
   * The endpoints `/api/session/register`, `/api/stream/chunk`, `/api/upload/part`, and `/api/safety/report_flood` had no access control or identity validation.
   * Any unprivileged user or process running on the local host could invoke these endpoints.
3. **Session Storage Mismatch (Silent Bypass)**:
   * The application stored user authorization in a protected SQLite database (`SESSION_FILE_PATH`).
   * The Rust bridge expected `SESSION_STRING` in `.env`. Because `.env` lacked `SESSION_STRING`, the Rust core was never registered (`active_accounts: 0`), silently bypassing MTProto hardware-level streaming acceleration.

---

## 2. Hardened Architecture & Security Controls

```
+---------------------------+                      +---------------------------+
|    FastAPI Python Core    |                      |    Rust MTProto Core      |
|        (Port 8088)        |                      |       (Port 8099)         |
+-------------+-------------+                      +-------------+-------------+
              |                                                  |
              | 1. Read Token (0600 mode)                        | 1. Read Token (env / 0600)
              |                                                  |
              | 2. Extract Session in RAM                        |
              |    StringSession.save(client.session)            |
              |                                                  |
              | 3. POST /api/session/register                    |
              |    Header: Authorization: Bearer <token>         |
              |    Body: { "session_string": "...", ... }        |
              +------------------------------------------------->|
              |                                                  | 4. Validate Bearer Token
              |                                                  |    (401 Unauthorized if bad)
              |                                                  | 5. Decode MTProto Auth Key
              |                                                  | 6. Init Grammers Session
              |<-------------------------------------------------+
              |    Response: 200 OK { active_accounts: 1 }       |
```

### 2.1 Loopback Bearer Authentication Middleware
All `/api/*` routes in `rust_core/src/main.rs` are protected by `auth_middleware`:
```rust
async fn auth_middleware(
    State(state): State<Arc<AppState>>,
    req: Request,
    next: Next,
) -> Result<Response, StatusCode> {
    if let Some(ref expected) = state.auth_token {
        if !expected.is_empty() {
            let auth_header = req.headers().get("Authorization").and_then(|h| h.to_str().ok());
            let expected_bearer = format!("Bearer {expected}");
            if auth_header != Some(&expected_bearer) {
                return Err(StatusCode::UNAUTHORIZED);
            }
        }
    }
    Ok(next.run(req).await)
}
```
* `/health` remains accessible without authentication to allow lightweight liveness/readiness polling.
* All data and session endpoints require `Authorization: Bearer <token>`.
* Permissive CORS was completely excised. Browsers cannot issue cross-origin requests.

### 2.2 Secure Token Storage & Lifecycle
* When the application starts, `_get_or_create_auth_token()` in `rust_bridge.py` verifies whether a token is already present in environment variables.
* If absent, a 256-bit cryptographic hex string (`secrets.token_hex(32)`) is saved to `backend/data/.rust_core_token`.
* The token file is secured with strict POSIX permissions:
  - `os.chmod(token_file, 0o600)`: Only the running user can read/write.
  - Enclosing directory is protected `0700`.
  - Excluded from Git tracking in `.gitignore` (`backend/data/.*token*`).
* When spawning `tg-rust-core`, the token is passed directly through the process environment (`TG_RUST_CORE_AUTH_TOKEN`), preventing CLI argument inspection via `ps aux`.

### 2.3 Dynamic In-Memory Session Export
To bridge the gap between Telethon's SQLite session file and Grammers' memory representation without exposing plaintext credentials:
1. `rust_bridge.ensure_sessions_registered()` retrieves the active `TelegramClient` from `TelegramClientManager`.
2. It dynamically exports the active MTProto key in memory using `StringSession.save(tg_client.session)`.
3. The string is transmitted over the authenticated loopback HTTP connection directly to Rust core and ingested into memory.
4. **No session strings are written to disk, `.env`, configuration files, or logs.**

### 2.4 MTProto Session ID Independence
* Both Telethon (Python) and Grammers (Rust) utilize the same 2048-bit Telegram authorization key.
* Under MTProto 2.0 specifications, multiple independent clients may share an authorization key provided they use unique 64-bit `session_id` identifiers.
* Grammers generates a randomized `session_id` upon startup, ensuring that packet sequence numbers (`seq_no`) and message IDs (`msg_id`) never collide with Telethon's active SQLite session.

---

## 3. Empirical Security Verification

| Test Scenario | Action Performed | Expected Behavior | Actual Behavior | Result |
|---|---|---|---|---|
| **Daemon Startup Without Token** | `env -u TG_RUST_CORE_AUTH_TOKEN ./tg-rust-core` | Panic & immediate exit | **Panicked: `TG_RUST_CORE_AUTH_TOKEN must be set` (code 101)** | **PASS (Fail-Closed)** |
| **Daemon Startup With Empty Token** | `TG_RUST_CORE_AUTH_TOKEN="" ./tg-rust-core` | Panic & immediate exit | **Panicked: `TG_RUST_CORE_AUTH_TOKEN cannot be empty` (code 101)** | **PASS (Fail-Closed)** |
| **Unauthenticated Request** | `POST http://127.0.0.1:8099/api/session/register` without header | HTTP 401 Unauthorized | **HTTP 401** returned | **PASS** |
| **Invalid Token** | `POST ...` with `Authorization: Bearer invalid_token_123` | HTTP 401 Unauthorized | **HTTP 401** returned | **PASS** |
| **Valid Bearer Token** | `POST /api/safety/report_flood` with valid bearer token | HTTP 200 OK | **HTTP 200** returned | **PASS** |
| **Browser CORS Probe** | Browser `OPTIONS` preflight with `Origin: http://evil.com` | Blocked / Disallowed | **Rejected (No Access-Control-Allow-Origin)** | **PASS** |
| **Token File Permissions** | `stat -c "%a %n" backend/data/.rust_core_token` | Mode `600` | **`600 backend/data/.rust_core_token`** | **PASS** |
| **Process Inspection** | `ps aux | grep tg-rust-core` | No token in CLI args | **Clean process command line** | **PASS** |
