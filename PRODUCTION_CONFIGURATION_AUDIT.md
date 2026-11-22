# Production Configuration & Cryptographic Audit

**Document Status:** Approved & Machine-Verified  
**Target Milestone:** TG Power Suite v3.0 Production Release Gate (Milestone 3.2)  
**Verification Date:** 2026-11-22  

---

## 1. Master Key Configuration Reconciliation

### 1.1 The Inconsistency Identified
During Phase 3.1 and earlier milestones, an environment variable naming discrepancy arose across configuration files, release reports, and code:
- `backend/app/security/encryption.py` originally inspected `SESSION_MASTER_KEY_B64` (base64 format) and `SESSION_MASTER_KEY` (raw bytes).
- `render.yaml`, `CLOUD_PERSISTENCE_TEST_REPORT.md`, and `FINAL_RELEASE_READINESS.md` referenced `SESSION_VAULT_MASTER_KEY` (described as 32-byte hex).

### 1.2 Canonical Configuration & Compatibility Resolution
To eliminate production deployment failure without breaking existing configurations or automated pipelines, `backend/app/security/encryption.py` and `backend/app/services/playback_grants.py` implement an explicit, safe resolution hierarchy:

```python
# 1. Canonical: SESSION_MASTER_KEY_B64 (Base64-encoded 32 bytes)
env_key = os.getenv("SESSION_MASTER_KEY_B64", "").strip()
if env_key:
    decoded = base64.b64decode(env_key)
    if len(decoded) == 32:
        return decoded

# 2. Compatibility Alias: SESSION_VAULT_MASTER_KEY (64-char Hex or Base64)
vault_key = os.getenv("SESSION_VAULT_MASTER_KEY", "").strip()
if vault_key:
    if len(vault_key) == 64:
        decoded = bytes.fromhex(vault_key)
        if len(decoded) == 32:
            return decoded
    decoded = base64.b64decode(vault_key)
    if len(decoded) == 32:
        return decoded
    if len(vault_key.encode("utf-8")) == 32:
        return vault_key.encode("utf-8")

# 3. Raw Legacy Alias: SESSION_MASTER_KEY (32 raw UTF-8 bytes)
raw_env = os.getenv("SESSION_MASTER_KEY", "").strip()
if raw_env and len(raw_env.encode("utf-8")) == 32:
    return raw_env.encode("utf-8")
```

### 1.3 Key Encoding and Validation Rules
| Parameter | Specification |
| :--- | :--- |
| **Algorithm** | AES-256-GCM (`cryptography.hazmat.primitives.ciphers.aead.AESGCM`) |
| **Exact Key Length** | **32 bytes (256 bits)** strictly enforced |
| **Canonical Format** | Base64-encoded string (44 characters, e.g. `openssl rand -base64 32`) |
| **Hex Format** | 64 hexadecimal characters (e.g. `openssl rand -hex 32`) |

---

## 2. Fail-Closed Production Security Gate

### 2.1 Prevention of Ephemeral Fallback Keys
In local development, if no master key is provided, the application generates a local file `.session_master_key` with permissions `0600`.

**In Production (`APP_ENV=production`, `RENDER=true`, or `ENVIRONMENT=production`):**
Generating an ephemeral fallback key is **strictly prohibited**. An ephemeral key would encrypt sessions that become completely unrecoverable upon container restart or redeploy.
The application fails fast during startup with `SessionEncryptionError`:
```text
CRITICAL: In production / Render deployment, encryption master key (SESSION_MASTER_KEY_B64 or SESSION_VAULT_MASTER_KEY) must be explicitly provided in environment variables. Generating an ephemeral master key is strictly prohibited as it causes permanent session loss on container restart.
```
Verified via automated unit test: `test_master_key_reconciliation.py::test_production_fails_closed_when_keys_missing` (PASSED).

---

## 3. Independent Backup & Disaster Recovery

The master encryption key **must never be stored in the PostgreSQL database**. If the database and master key were colocated, a database compromise or backup leak would expose all encrypted Telegram session strings.

### 3.1 Operator Runbook: Key Generation & Injection
1. **Generate Master Key:**
   ```bash
   # Canonical Base64 Key (32 bytes)
   openssl rand -base64 32
   # Or Hex Key (64 characters)
   openssl rand -hex 32
   ```
2. **Inject via Render Environment:**
   Set `SESSION_VAULT_MASTER_KEY` or `SESSION_MASTER_KEY_B64` in the Render Web Service Settings -> Environment Variables. Mark as secret (`sync: false` in `render.yaml`).
3. **Backup Master Key Securely:**
   Store the key offline in a password manager (1Password, Bitwarden) or enterprise secret manager (AWS Secrets Manager, HashiCorp Vault). Never commit to Git or push to container registries.

### 3.2 Disaster Recovery Procedure
- If a container is recreated or upgraded: As long as `SESSION_VAULT_MASTER_KEY` / `SESSION_MASTER_KEY_B64` matches the original key, all session records in PostgreSQL remain 100% decryptable.
- If the master key is lost: Encrypted session payloads cannot be decrypted. Users must re-authenticate their Telegram accounts.

---

## 4. Authentication Credential Hashing Audit (Argon2id vs PBKDF2)

### 4.1 Documentation Discrepancy Reconciliation
Certain earlier planning and audit documents cited "Argon2id credential hashing". An audit of the codebase confirms that:
- `backend/app/auth_v3/identity_service.py` uses **PBKDF2-HMAC-SHA256**.
- The standard library `hashlib.pbkdf2_hmac` is used to prevent external C-dependency failures across Alpine and Debian Docker images.

### 4.2 Algorithm Parameters & Security Conformance
```python
salt = os.urandom(16)
key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 600000)
return f"pbkdf2_sha256$600000${salt.hex()}${key.hex()}"
```
- **Iterations:** **600,000 rounds** (strictly meeting and exceeding the OWASP 2024 Password Storage Cheat Sheet recommendation of 600,000 iterations for PBKDF2-HMAC-SHA256).
- **Salt:** 16 bytes of cryptographically secure pseudo-random data (`os.urandom(16)`).
- **Key Length:** 32 bytes (256 bits).
- **Constant-Time Verification:** Verified with `hmac.compare_digest` to prevent timing attacks.
- **Test Verification:** Verified via `test_master_key_reconciliation.py::test_password_hashing_algorithm_is_pbkdf2_sha256` (PASSED).
