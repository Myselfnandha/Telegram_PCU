# TG POWER SUITE — STREAMING REMEDIATION ROADMAP

**Audit Scope**: Engineering Fix Plan for Root Causes Identified in Two-Day Forensic Investigation  
**Priority Hierarchy**:
* **P0 (Critical / Immediate)**: Fixes throughput bottlenecks, task leaks, and runtime generator crashes.
* **P1 (High Priority)**: Eliminates playback stalls, VLC probe thrashing, and inaccurate task tracking.
* **P2 (Medium Priority)**: Hardens buffer runway dynamics, loop binding, and error resilience.
* **P3 (Observability / Hygiene)**: Enables production logging for daemon subprocesses.

---

## 1. Remediation Summary Table

| Priority | ID | Target Component | File Path & Lines | Core Fix Description |
|---|---|---|---|---|
| **P0** | FIX-001 | Rust Bridge Registration | `backend/app/services/rust_bridge.py:157-172` | Export string session dynamically from Telethon SQLite session into Rust core. |
| **P0** | FIX-002 | Async Generator Lifecycle | `backend/app/routes/proxy_streaming.py:683-688` | Safe generator teardown: guard `aclose()` against already-running generators. |
| **P0** | FIX-003 | Producer Task Cancellation | `backend/app/services/stream_engine.py:1039-1044` | Drain queue, remove 0.8s timeout, and cleanly await producer cancellation. |
| **P1** | FIX-004 | VLC Probe Debouncing | `backend/app/routes/proxy_streaming.py:619-638` | Do not cancel active media streams on small metadata/tail probes ($\le 512\text{KB}$). |
| **P1** | FIX-005 | Stream Task Tracking | `backend/app/routes/proxy_streaming.py:645, 650` | Store `handle.task` (streaming coroutine) instead of `curr_task` (route handler). |
| **P2** | FIX-006 | Adaptive Buffer Runway | `backend/app/services/stream_engine.py:761-770` | Dynamically scale lookahead buffer when wire throughput is $< 1.5\text{MB/s}$. |
| **P2** | FIX-007 | Telethon Loop Resilience | `backend/app/telegram_client.py:405-430` | Lazy event loop binding to prevent startup loop invalidation during reload. |
| **P3** | FIX-008 | Rust Core Observability | `backend/app/services/rust_bridge.py:101-106` | Redirect Rust subprocess stdout/stderr to `backend/data/rust_core.log`. |

---

## 2. Detailed Technical Fix Specifications

### FIX-001: Dynamic Telethon Session Export into Rust Core (P0)

* **Target File**: `backend/app/services/rust_bridge.py:157-172`
* **Problem**: `ensure_sessions_registered()` requires `TG_SESSION_STRING` in `os.environ`, but credentials exist only as SQLite `.session` files on disk, causing Rust core to remain at `active_accounts: 0`.
* **Minimal Safe Code Diff**:
  ```python
  # backend/app/services/rust_bridge.py
  async def ensure_sessions_registered(self) -> bool:
      if getattr(self, "_registering_sessions", False):
          return False
      self._registering_sessions = True
      try:
          reg_any = False
          # 1. First check environment variables
          s1 = SESSION_STRING
          s2 = SESSION_STRING_2
          
          # 2. If empty, export string session dynamically from active Telethon client!
          if not s1 or not s2:
              try:
                  from app.telegram_client import TelegramClientWrapper
                  client = await TelegramClientWrapper.get_client()
                  if client and client.is_connected() and hasattr(client, "session"):
                      from telethon.sessions import StringSession
                      if not s1:
                          s1 = StringSession.save(client.session)
                  sec_client = getattr(TelegramClientWrapper, "_secondary_client", None)
                  if sec_client and sec_client.is_connected() and hasattr(sec_client, "session"):
                      if not s2:
                          s2 = StringSession.save(sec_client.session)
              except Exception as exp_err:
                  logger.debug(f"Dynamic session export notice: {exp_err}")

          if s1 and API_ID:
              ok1 = await self.register_session(s1, API_ID, account_idx=0)
              reg_any = reg_any or ok1
          if s2 and API_ID:
              ok2 = await self.register_session(s2, API_ID, account_idx=1)
              reg_any = reg_any or ok2
          return reg_any
      finally:
          self._registering_sessions = False
  ```
* **Acceptance Criteria**:
  * `curl http://127.0.0.1:8099/health` returns `"active_accounts": 1` (or `2`).
  * `rust_bridge.is_healthy()` evaluates to `True`.
  * Subsequent stream chunks route through `/api/stream/chunk`, achieving $> 15\text{MB/s}$ wire throughput.
* **Regression Risk**: **Low**. If session export fails, the code safely falls back to current behavior.

---

### FIX-002: Safe Generator Teardown in Stream Wrapper (P0)

* **Target File**: `backend/app/routes/proxy_streaming.py:683-688`
* **Problem**: `await gen.aclose()` in the `finally:` block of `stream_wrapper` conflicts with Python's async generator cancellation unwinding, causing `RuntimeError: aclose(): asynchronous generator is already running`.
* **Minimal Safe Code Diff**:
  ```python
  # backend/app/routes/proxy_streaming.py
          finally:
              if hasattr(gen, "aclose"):
                  try:
                      # If generator is already executing or being closed by event loop, skip explicit aclose
                      import inspect
                      if inspect.getasyncgenstate(gen) != inspect.AGEN_RUNNING:
                          await gen.aclose()
                  except (RuntimeError, Exception, asyncio.CancelledError):
                      pass
  ```
* **Acceptance Criteria**:
  * Zero occurrences of `RuntimeError: aclose(): asynchronous generator is already running` in logs during rapid seek operations.
* **Regression Risk**: **Near Zero**. Standard Python PEP 525 introspection check.

---

### FIX-003: Producer Task Cancellation Cleanup & Queue Draining (P0)

* **Target File**: `backend/app/services/stream_engine.py:1039-1044` and `stream_engine.py:905`
* **Problem**: Producer task cancellation times out after 800ms when Telegram socket is slow, leaving 93 orphaned tasks in memory. Producer also hangs on `await queue.put(None)` at line 905 when queue is full.
* **Minimal Safe Code Diff**:
  1. In `stream_engine.py:905`:
     ```python
     # Replace blocking queue.put with put_nowait / drain guard:
     try:
         queue.put_nowait(None)
     except asyncio.QueueFull:
         try:
             queue.get_nowait()
             queue.put_nowait(None)
         except Exception:
             pass
     ```
  2. In `stream_engine.py:1039-1044`:
     ```python
     if producer_task and not producer_task.done():
         producer_task.cancel()
         # Empty the queue so producer cannot block on queue.put()
         while not queue.empty():
             try:
                 queue.get_nowait()
             except Exception:
                 break
         try:
             # Shielded cancellation await with graceful 3.0s window
             await asyncio.wait_for(asyncio.shield(producer_task), timeout=3.0)
         except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
             pass
     ```
* **Acceptance Criteria**:
  * Zero occurrences of `Task was destroyed but it is pending!` during intense seeking or slow DC connections.
* **Regression Risk**: **Low**. Prevents task leaking and eliminates deadlock on queue puts.

---

### FIX-004: VLC Metadata Probe Request Debouncing (P1)

* **Target File**: `backend/app/routes/proxy_streaming.py:619-638`
* **Problem**: VLC metadata probes (`bytes=0-`, tail probes, `bytes=107-`) immediately abort running video playback streams.
* **Minimal Safe Code Diff**:
  ```python
  # backend/app/routes/proxy_streaming.py
  # Only cancel active playback iterators if the new request is NOT a probe request!
  if curr_task and is_media_player and not is_probe_request:
      host_player_keys = [
          k for k in list(_ACTIVE_STREAM_ITERS.keys())
          if len(k) >= 4 and k[3] == norm_client_host and (k[2] == client_tag or (k[0] == norm_chat_id and k[1] == message_id)) and (len(k) < 5 or k[4] != id(curr_task))
      ]
      for sk in host_player_keys:
          # Cancel only previous full-length playback streams, NOT probe tasks
          old_h = _ACTIVE_STREAM_ITERS.pop(sk, None)
          if old_h:
              old_h.stop_event.set()
          old_task_tuple = _ACTIVE_STREAM_TASKS.pop(sk, None)
          if old_task_tuple:
              old_t, _ = old_task_tuple
              if old_t and not old_t.done() and old_t is not curr_task:
                  old_t.cancel()
          old_cid, old_mid = sk[0], sk[1]
          stream_engine.advance_seek_generation(old_cid, old_mid, start)
          _ACTIVE_MEDIA_CONNS.pop((old_cid, old_mid), None)
  ```
* **Acceptance Criteria**:
  * Opening an MKV file allows metadata probes to read headers without cancelling the primary in-flight media stream.
* **Regression Risk**: **Low**. Preserves seek cancellation for actual user timeline seeks (`start > 0` and `length > 512KB`).

---

### FIX-005: Accurate Response Streaming Task Tracking (P1)

* **Target File**: `backend/app/routes/proxy_streaming.py:645, 650`
* **Problem**: `_ACTIVE_STREAM_TASKS` stores `curr_task` (the route handler task that exits immediately), rendering `old_t.cancel()` ineffective during subsequent seeks.
* **Minimal Safe Code Diff**:
  ```python
  # backend/app/routes/proxy_streaming.py:650
      async def stream_wrapper():
          streaming_task = asyncio.current_task()
          handle.task = streaming_task
          if not is_probe_request:
              _ACTIVE_STREAM_TASKS[session_key] = (streaming_task, start)
  ```
* **Acceptance Criteria**:
  * When a user seeks, `old_t.cancel()` cancels the actual running `StreamingResponse` task immediately rather than waiting for next generator yield.
* **Regression Risk**: **Low**.

---

### FIX-006: Telethon Event Loop Binding Hardening (P2)

* **Target File**: `backend/app/telegram_client.py:405-430`
* **Problem**: `cls._client` bound to old event loop during Uvicorn reload, generating 50 errors: `The asyncio event loop must not change after connection`.
* **Minimal Safe Code Diff**:
  ```python
  # backend/app/telegram_client.py:410
  current_loop = asyncio.get_running_loop()
  if cls._client is not None and getattr(cls._client, "loop", None) is not current_loop:
      logger.info("Detected event loop change in Telethon client. Re-initializing client for active loop...")
      try:
          if cls._client.is_connected():
              asyncio.run_coroutine_threadsafe(cls._client.disconnect(), cls._client.loop)
      except Exception:
          pass
      cls._client = None
  ```
* **Acceptance Criteria**:
  * Clean Uvicorn reload without any `The asyncio event loop must not change after connection` or `database is locked` errors.

---

### FIX-007: Subprocess Observability for Rust Daemon (P3)

* **Target File**: `backend/app/services/rust_bridge.py:101-106`
* **Problem**: Rust daemon output is piped to `DEVNULL`.
* **Minimal Safe Code Diff**:
  ```python
  # backend/app/services/rust_bridge.py:101
  rust_log_path = ROOT_DIR / "backend" / "data" / "rust_core.log"
  rust_log_file = open(rust_log_path, "a", encoding="utf-8")
  self._process = subprocess.Popen(
      [str(path)],
      stdout=rust_log_file,
      stderr=subprocess.STDOUT,
      close_fds=True
  )
  ```
* **Acceptance Criteria**:
  * All Rust core logs, panics, and grammers warnings are captured in `backend/data/rust_core.log`.
