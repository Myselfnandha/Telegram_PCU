# Render Staging & Deployment Verification

**Document Status:** Approved & Audited  
**Target Platform:** Render Cloud Application (Docker Runtime + External PostgreSQL)  
**Verification Date:** 2026-11-22  

---

## 1. Render Blueprint & Secret Isolation Audit

### 1.1 Secret Management in `render.yaml`
An audit of `render.yaml` confirms that **zero secrets are hardcoded or tracked in Git**:
- `DATABASE_URL`: Set to `sync: false` (injected via Render secret dashboard).
- `SESSION_VAULT_MASTER_KEY`: Set to `sync: false` (injected via Render secret dashboard).
- `TG_API_ID`, `TG_API_HASH`, `TG_SESSION_STRING`: Set to `sync: false`.
- `API_AUTH_TOKEN`: Configured with `generateValue: true` for automatic cryptographic token generation.
- `ADMIN_BOOTSTRAP_TOKEN`: Configured with `generateValue: true` for secure one-time initial operator onboarding.

### 1.2 Dockerfile Security Hardening
- **Multi-Stage Build:** 4 isolated build stages (Node 22 frontend builder, Rust slim accelerator builder, Python builder, runtime image).
- **Non-Root Execution:** Dedicated non-root user `nonroot:nonroot` (UID 10001).
- **Directory Permissions:** `sessions/` directory locked down to `0700`.
- **Healthcheck:** Configured with `curl -f http://localhost:${PORT:-8088}/api/system/health || exit 1`.

---

## 2. Effective Production Feature Flags

| Feature Flag | Effective Setting | Verification Purpose |
| :--- | :---: | :--- |
| `MULTI_TENANT_ENABLED` | `true` | Enables multi-user identity service, session vault, and device management. |
| `SESSION_VAULT_ENABLED` | `true` | Enforces AES-256-GCM encryption for all Telegram MTProto session strings. |
| `MULTI_TENANT_STREAMING_ENABLED` | `true` | Routes media streaming through tenant-authorized Telegram clients. |
| `ADAPTIVE_MULTI_TURBO_ENABLED` | `true` | Activates adaptive multi-account parallel chunk downloads for accounts with verified media access. |
| `RUST_MULTI_TENANT_ENABLED` | `false` | **Disabled by default.** Multi-tenant routing uses the mature Python Telethon SenderPool to eliminate memory safety and concurrency risks during initial v3.0 rollout. Single-tenant Rust core acceleration remains available as fallback. |
| `DEVICE_VERIFICATION_REQUIRED` | `true` | Enforces operator / user approval for new client device UUIDs. |

---

## 3. Database Migration Concurrency Protection

In multi-instance cloud deployments, concurrent schema migrations can result in race conditions, deadlock, or duplicate index errors (`pg_type_typname_nsp_index`).

### Safeguards Implemented:
1. **Single-Process Foreground Startup:** The container starts via `python backend/run.py start --foreground`, running a single master process per Render web instance.
2. **Metadata Creation Guard:** `backend/app/db/session.py` invokes `Base.metadata.create_all` within an atomic transaction block (`engine.begin()`).
3. **Idempotent Migration Path:** Pre-deployment schema updates can be executed via `alembic upgrade head` before worker activation.

---

## 4. GitHub Actions Quota Zero-Minute Architecture

### 4.1 Root Cause of Quota Exhaustion
The user reported: `"my github action has reached 100% limit for workflows"`. This occurs when CI/CD workflows run on every push or branch commit, burning through GitHub Actions free-tier minutes.

### 4.2 Zero-Minute Render Build Architecture
The deployment pipeline is architected to require **0 GitHub Actions minutes**:
1. **Render Native Git Build:** Render connects directly to the GitHub repository via webhook. When a commit is merged to `main`, Render pulls the repository and builds the multi-stage `Dockerfile` on **Render's own cloud build servers**, consuming **0 GitHub Actions minutes**.
2. **Workflow Audit:**
   - `.github/workflows/deploy.yml`: Configured with `on: workflow_dispatch` only. Does not run on push.
   - `.github/workflows/ci.yml`: Configured with `on: workflow_dispatch` and `pull_request: branches: [main]` (with path ignores for `.md`, `docs/`, `render.yaml`). Feature branch pushes consume 0 minutes.
   - `.github/workflows/release.yml`: Configured with `on: push: tags: ['v*']` only. Standard development commits consume 0 minutes.
