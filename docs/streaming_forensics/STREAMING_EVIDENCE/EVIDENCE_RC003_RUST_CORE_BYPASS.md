# FORENSIC EVIDENCE: RC-003 RUST MTPROTO CORE SILENT BYPASS

**Target Subsystems**: `backend/app/services/rust_bridge.py:52-76, 157-172`, `backend/app/config.py:80-84`  
**Evidence Source**: Live endpoint interrogation and log audit of `tg_power_suite.log`  
**Verdict**: 100% of chunks fell back to Python Telethon; 0 chunks streamed via Rust core.

---

## 1. Live Endpoint Verification

Direct HTTP query to the active Rust daemon process (`tg-rust-core`, PID 2422) on loopback port 8099:

```bash
$ curl -s http://127.0.0.1:8099/health
{"status":"ok","engine":"Rust MTProto (grammers)","active_accounts":0,"rate_limit_mbs":24.0}
```

### Analysis:
* The Rust daemon process is running and returns HTTP 200 OK.
* The daemon reports `"active_accounts": 0`.
* In `rust_bridge.py:64`:
  ```python
  ok = data.get("status") == "ok" and active > 0
  ```
  Since `active == 0`, `is_healthy()` evaluates to `False`.

---

## 2. Configuration & Credential Mismatch

Inspection of `backend/app/config.py:80-84` vs session storage on disk:

```python
# backend/app/config.py
SESSION_STRING = os.getenv("TG_SESSION_STRING", "").strip()
SESSION_STRING_2 = os.getenv("TG_SESSION_STRING_2", "").strip()
```

Environment evaluation:
```python
>>> from app.config import API_ID, SESSION_STRING, SESSION_STRING_2
>>> print(bool(API_ID), bool(SESSION_STRING), bool(SESSION_STRING_2))
True False False
```

File system inspection:
```bash
$ ls -la backend/sessions/
-rw------- 1 nandha nandha  45056 Nov 16 19:33 tg_suite_user_session.session
-rw------- 1 nandha nandha  45056 Nov 16 19:33 tg_suite_secondary_session.session
```

### The Registration Deadlock:
In `rust_bridge.py:163-169`:
```python
if SESSION_STRING and API_ID:
    ok1 = await self.register_session(SESSION_STRING, API_ID, account_idx=0)
if SESSION_STRING_2 and API_ID:
    ok2 = await self.register_session(SESSION_STRING_2, API_ID, account_idx=1)
```
Because `SESSION_STRING` and `SESSION_STRING_2` are empty strings, `register_session()` was never called across the entire two-day operational window.

---

## 3. Log Evidence of Health Polling Loop

FastAPI executed **3,822 health checks** against port 8099, but zero stream chunks were dispatched:

```log
2026-11-17 00:00:02,142 [INFO] [httpx] HTTP Request: GET http://127.0.0.1:8099/health "HTTP/1.1 200 OK"
2026-11-17 00:00:04,145 [INFO] [httpx] HTTP Request: GET http://127.0.0.1:8099/health "HTTP/1.1 200 OK"
...
2026-11-18 23:28:11,984 [INFO] [httpx] HTTP Request: GET http://127.0.0.1:8099/health "HTTP/1.1 200 OK"
2026-11-18 23:28:12,081 [INFO] [httpx] HTTP Request: GET http://127.0.0.1:8099/health "HTTP/1.1 200 OK"
```

Endpoints queried in `tg_power_suite.log`:
* `GET /health`: **3,822 times** (100.0%)
* `POST /api/stream/chunk`: **0 times** (0.0%)
* `POST /api/stream/prewarm`: **0 times** (0.0%)
* `POST /api/session/register`: **0 times** (0.0%)
