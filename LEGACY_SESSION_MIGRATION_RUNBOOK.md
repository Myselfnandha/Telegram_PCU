# TG Power Suite v3.0 — Legacy Session Migration Runbook

**Document**: Runbook & Operations Guide  
**Purpose**: Safely migrate legacy single-user SQLite sessions into the multi-tenant encrypted vault  
**Script**: [`backend/scripts/migrate_legacy_sessions.py`](file:///home/nandha/Desktop/TG_TOOL/backend/scripts/migrate_legacy_sessions.py)  

---

## 1. Prerequisites & Safety Invariants

1. **Original Files Preserved**: The migration tool reads legacy SQLite files (`tg_suite_user_session.session`, `tg_suite_secondary_session.session`) in read-only mode. It **NEVER deletes, alters, or moves** legacy session files.
2. **Explicit Owner Designation**: Legacy sessions are never automatically bound to arbitrary users. An authenticated `owner_user_id` or an explicit admin creation flag (`--create-admin`) must be specified.
3. **Encrypted Backup on Disk**: An encrypted archive (`backend/backup/v2_legacy_sqlite_backup.tar.gz.enc`, `0600` permissions) exists and has been verified.
4. **Idempotence**: Running the migration multiple times will not create duplicate accounts or corrupt vault entries.

---

## 2. Step-by-Step Execution Guide

### Step 1: Inspect Available Legacy Sessions
Run the inspect command to check detected sessions and their MTProto Data Center (DC) identities without making database changes:

```bash
./venv/bin/python backend/scripts/migrate_legacy_sessions.py --inspect
```

Expected JSON Output:
```json
{
  "primary_telethon": {
    "path": ".../backend/sessions/tg_suite_user_session.session",
    "exists": true,
    "size_bytes": 73728,
    "dc_id": 5,
    "server_address": "91.108.56.150",
    "port": 443,
    "has_auth_key": true
  },
  "secondary_telethon": {
    "path": ".../backend/sessions/tg_suite_secondary_session.session",
    "exists": true,
    "size_bytes": 28672,
    "dc_id": 2,
    "server_address": "149.154.167.51",
    "port": 443,
    "has_auth_key": true
  }
}
```

---

### Step 2: Perform a Dry-Run Migration
Validate that session strings can be parsed, encrypted, and written without modifying the database:

```bash
./venv/bin/python backend/scripts/migrate_legacy_sessions.py \
  --create-admin \
  --admin-name "Production Admin" \
  --primary-tg-id <YOUR_PRIMARY_TELEGRAM_USER_ID> \
  --secondary-tg-id <YOUR_SECONDARY_TELEGRAM_USER_ID> \
  --dry-run
```

Expected Result:
- `Success: True`
- `Dry Run: True`
- `Notes: Dry run completed: no database modifications were made.`

---

### Step 3: Execute Live Migration
Perform the live migration and cryptographic round-trip verification:

```bash
./venv/bin/python backend/scripts/migrate_legacy_sessions.py \
  --create-admin \
  --admin-name "Production Admin" \
  --primary-tg-id <YOUR_PRIMARY_TELEGRAM_USER_ID> \
  --secondary-tg-id <YOUR_SECONDARY_TELEGRAM_USER_ID>
```

Expected Result:
- Admin user registered in `app_users`
- Primary account registered in `telegram_accounts` (`role='primary'`)
- Secondary Turbo account registered (`role='turbo'`)
- Encrypted credentials written to `telegram_session_vault`
- Decryption verification round-trip passed

---

## 3. Rollback Procedure

If issues occur during the verification stabilization window:

1. Retrieve the designated owner's UUID and migrated account IDs:
   ```bash
   sqlite3 backend/data/tg_power_suite_v3.db "SELECT id, owner_user_id, role, telegram_user_id FROM telegram_accounts;"
   ```
2. Delete the migrated vault entries and accounts:
   ```bash
   sqlite3 backend/data/tg_power_suite_v3.db "DELETE FROM telegram_session_vault; DELETE FROM telegram_accounts;"
   ```
3. The legacy v2.0.0 system continues to function immediately using `backend/sessions/tg_suite_user_session.session` because the legacy files were never modified.
