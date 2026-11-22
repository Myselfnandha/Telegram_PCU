# TG POWER SUITE — STREAMING ROOT-CAUSE ANALYSIS

**Audit Period**: `2026-11-17 00:00:00 IST` to `2026-11-18 23:29:39 IST`  
**Investigation Framework**: Formal SRE & Streaming Systems Forensic Classification  
**Confidence Standard**:
* **CONFIRMED**: Backed by deterministic source code logic, reproducible trigger conditions, and exact log timestamp correlation.
* **HIGH CONFIDENCE**: Strongly substantiated by multi-source telemetry and timing signatures; alternative hypotheses excluded.
* **PROBABLE**: Supported by observable symptoms and system behavior; directly verifiable with instrumented tracing.

---

## 1. Root Cause Summary Matrix

| ID | Title | Confidence | Affected Layer | Impact Level |
|---|---|---|---|---|
| **RC-001** | VLC Range Probe Storm & Premature Stream Iterator Cancellation | **CONFIRMED** | HTTP Routing / Protocol | **P0 (Severe Playback Stall)** |
| **RC-002** | Producer Task Cancellation Timeout (800ms) Leaks Orphaned Tasks | **CONFIRMED** | Async Engine Concurrency | **P0 (Memory/Task Leak)** |
| **RC-003** | Rust MTProto Core Silent Bypass via Env String vs SQLite Session Mismatch | **CONFIRMED** | Rust/Python Bridge Integration | **P0 (Throughput Degradation)** |
| **RC-004** | Async Generator `aclose()` Collision During Concurrent Route Abort | **CONFIRMED** | Python Async Generator Lifecycle | **P1 (Uncaught Runtime Error)** |
| **RC-005** | Telegram Single-Connection Bandwidth Deficit vs Player Rate | **HIGH CONFIDENCE** | MTProto Network Transport | **P1 (Buffer Runway Depletion)** |
| **RC-006** | Global `TelegramClient` Class Attribute Event Loop Invalidation | **CONFIRMED** | Application Lifecycle / SQLite | **P2 (Startup Error Flood)** |
| **RC-007** | Route Handler Task Stored in Place of Streaming Task in Connection Map | **CONFIRMED** | Stream Session State Tracking | **P1 (Zombie Stream Leak)** |

---

## 2. In-Depth Root Cause Breakdown

### RC-001: VLC Range Probe Storm & Premature Stream Iterator Cancellation

* **Formal Classification**: **CONFIRMED**
* **Affected Subsystems**: `backend/app/routes/proxy_streaming.py:619-638`, `backend/app/services/stream_engine.py:1032-1057`
* **Trigger Mechanism**:
  When VLC opens any Matroska (`.mkv`) container, its demuxer executes an aggressive metadata discovery sequence:
  1. Requests EBML Header: `Range: bytes=0-`
  2. Within 50–300ms, seeks to file tail to locate MKV Cues/SeekHead: `Range: bytes=<file_size - 22KB>-`
  3. Immediately requests Segment Header: `Range: bytes=107-` (or `121-` / `300-`)
  4. Requests secondary EBML metadata / void elements: `Range: bytes=<last 665 bytes>-`
  5. Requests initial media playback offset: `Range: bytes=<target_offset>-`
  
  In `backend/app/routes/proxy_streaming.py:619-638`:
  ```python
  if curr_task and is_media_player:
      host_player_keys = [
          k for k in list(_ACTIVE_STREAM_ITERS.keys())
          if len(k) >= 4 and k[3] == norm_client_host and (k[2] == client_tag or (k[0] == norm_chat_id and k[1] == message_id)) and (len(k) < 5 or k[4] != id(curr_task))
      ]
      for sk in host_player_keys:
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
  Every incoming request immediately cancels all previous active tasks and triggers `advance_seek_generation()`.
* **Forensic Evidence**:
  * In `The Threesome (2025)` (Nov 17): 4 probe requests arrived in 82 milliseconds (`bytes=0-` $\to$ `bytes=1014614652-` $\to$ `bytes=300-` $\to$ `bytes=132-`).
  * In `Fatal Seduction S01E07` (Nov 17): 71 requests in 77 seconds.
  * In `Celebrity Master Chef EP21` (Nov 18, `23:28:05`–`23:28:06`): 5 probe requests within 1.1 seconds (`bytes=0-`, `bytes=1595910547-`, `bytes=107-`, `bytes=1595931922-`, `bytes=121-`).
* **Downstream Effects**:
  Before the backend can complete a 1MB Telegram chunk download for request $N$, request $N+1$ aborts request $N$. Wire tasks are cancelled, TCP sockets discard in-flight bytes, Telegram API request counters increment unnecessarily, and the player suffers high startup latency.

---

### RC-002: Producer Task Cancellation Timeout (800ms) Leaks Orphaned Tasks

* **Formal Classification**: **CONFIRMED**
* **Affected Subsystems**: `backend/app/services/stream_engine.py:1039-1044`, `backend/app/services/stream_engine.py:905`
* **Trigger Mechanism**:
  Inside `stream_engine.py:stream_media()`, chunk fetching is decoupled into a producer task (`pipelined_chunk_producer`) feeding an `asyncio.Queue`. When the outer `stream_media` generator exits (due to player disconnect, stream completion, or seek abort), the `finally:` block attempts to cancel the producer:
  ```python
  if producer_task and not producer_task.done():
      producer_task.cancel()
      try:
          await asyncio.wait_for(producer_task, timeout=0.8)
      except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
          pass
  ```
  When the producer is awaiting a Telegram MTProto chunk from a DC over a congested socket (which can take 1.5s to 5.2s), Telethon's network read does not cancel instantly.
  Because the wait timeout is hardcoded to only **0.8 seconds**:
  1. `asyncio.wait_for` raises `asyncio.TimeoutError`.
  2. The exception is swallowed by `pass`.
  3. `stream_media` exits, and the generator object is destroyed.
  4. The `producer_task` remains alive in the asyncio event loop as a detached background task.
  5. The producer eventually completes or is garbage collected, logging the fatal warning:
     `Task was destroyed but it is pending! task: <Task pending name='Task-...' coro=<StreamEngine.stream_media.<locals>.pipelined_chunk_producer() ...>>`.
* **Forensic Evidence**:
  * Exactly **93 occurrences** of `Task was destroyed but it is pending!` logged between `18:04:24` and `18:12:36` on Nov 17 during the playback of `Stepdaddy (2026)`.
  * Every single one of the 93 leaked tasks was `coro=<StreamEngine.stream_media.<locals>.pipelined_chunk_producer() running at /home/nandha/Desktop/TG_TOOL/backend/app/services/stream_engine.py:905>`.
  * Line 905 is: `await queue.put(None)`. The orphaned producer was stuck trying to put `None` into an asyncio queue whose consumer had already vanished!
* **Downstream Effects**:
  Severe memory leak of task objects, unbounded CPU consumption from orphaned MTProto socket reads, and socket exhaustion on Telegram DC endpoints.

---

### RC-003: Rust MTProto Core Silent Bypass via Env String vs SQLite Session Mismatch

* **Formal Classification**: **CONFIRMED**
* **Affected Subsystems**: `backend/app/services/rust_bridge.py:52-76, 157-172`, `backend/app/config.py:73-84`
* **Trigger Mechanism**:
  1. TG Power Suite runs `tg-rust-core` daemon on port 8099.
  2. When FastAPI polls `http://127.0.0.1:8099/health`, the Rust core returns:
     `{"status":"ok","engine":"Rust MTProto (grammers)","active_accounts":0,"rate_limit_mbs":24.0}`.
  3. In `rust_bridge.py:63-65`:
     ```python
     active = data.get("active_accounts", 0)
     ok = data.get("status") == "ok" and active > 0
     if data.get("status") == "ok" and active == 0:
         asyncio.create_task(self.ensure_sessions_registered())
     return ok
     ```
     Because `active_accounts == 0`, `is_healthy()` returns `False`.
  4. In `rust_bridge.py:157-172`:
     ```python
     async def ensure_sessions_registered(self) -> bool:
         ...
         if SESSION_STRING and API_ID:
             ok1 = await self.register_session(SESSION_STRING, API_ID, account_idx=0)
         if SESSION_STRING_2 and API_ID:
             ok2 = await self.register_session(SESSION_STRING_2, API_ID, account_idx=1)
     ```
  5. In `backend/app/config.py:80-84`, `SESSION_STRING` is loaded from `os.getenv("TG_SESSION_STRING", "").strip()`.
  6. However, TG Power Suite stores its actual authenticated Telegram credentials as **SQLite `.session` files** on disk (`backend/sessions/tg_suite_user_session.session`). `TG_SESSION_STRING` in `.env` is empty!
  7. Therefore, `ensure_sessions_registered()` never executes `register_session()`. `active_accounts` stays `0` forever.
* **Forensic Evidence**:
  * Out of **3,822 HTTP requests** to `127.0.0.1:8099` in `tg_power_suite.log`, 100% were `GET /health`.
  * Exactly **0 calls** were made to `/api/stream/chunk` or `/api/stream/prewarm` on Nov 17 and Nov 18.
  * Direct query to live endpoint `http://127.0.0.1:8099/health` confirmed `active_accounts: 0`.
* **Downstream Effects**:
  The entire Rust streaming engine (designed to deliver 24.0 MB/s multi-account throughput) was completely dead code at runtime. 100% of all 6,480 chunks across both days were forced through Python Telethon, causing severe performance degradation and buffer starvation during high-bitrate streaming.

---

### RC-004: Async Generator `aclose()` Collision During Concurrent Route Abort

* **Formal Classification**: **CONFIRMED**
* **Affected Subsystems**: `backend/app/routes/proxy_streaming.py:683-688`
* **Trigger Mechanism**:
  In `proxy_streaming.py:683-688`:
  ```python
  finally:
      if hasattr(gen, "aclose"):
          try:
              await gen.aclose()
          except (RuntimeError, Exception, asyncio.CancelledError):
              pass
  ```
  When a prior streaming task is cancelled via `old_t.cancel()` from line 632:
  1. The outer coroutine running `stream_wrapper` receives `asyncio.CancelledError`.
  2. If the inner async generator `gen` is currently suspended in an `await` expression, the asyncio runtime begins an asynchronous throw (`athrow`) into `gen` to trigger its own `CancelledError`.
  3. Before `athrow` completes, the outer `finally:` block executes `await gen.aclose()`.
  4. Python PEP 525 rules prohibit calling `aclose()` while the generator is already running or being closed by the event loop.
  5. Python immediately raises `RuntimeError: aclose(): asynchronous generator is already running`.
  6. Because this occurs inside the generator teardown future, it is caught by Python's unhandled task exception handler and logged to stderr.
* **Forensic Evidence**:
  * Exactly **16 occurrences** of `RuntimeError: aclose(): asynchronous generator is already running` logged on Nov 17.
  * Example from `The Threesome (2025)` at `17:28:56,254`:
    ```
    [ERROR] [asyncio] Task exception was never retrieved
    future: <Task finished name='Task-3703' coro=<<async_generator_athrow without __name__>()> exception=RuntimeError('aclose(): asynchronous generator is already running')>
    RuntimeError: aclose(): asynchronous generator is already running
    ```
* **Downstream Effects**:
  Generates false-positive error noise in monitoring logs and leaves inner generator cleanup blocks partially executed.

---

### RC-005: Telegram Single-Connection Bandwidth Deficit vs Player Consumption Rate

* **Formal Classification**: **HIGH CONFIDENCE**
* **Affected Subsystems**: `backend/app/services/stream_engine.py:440-520`, MTProto DC 4/5 Transport
* **Trigger Mechanism**:
  * High-definition 1080p video files (e.g. `Celebrity Master Chef EP20`, 1.45GB for 45 minutes) require a steady playback bitrate of ~0.65–0.75 MB/s, with initial buffering spikes of 2.0–4.0 MB/s.
  * In single-client Python MTProto mode (forced by RC-003), chunk downloads from Telegram DC 4 and DC 5 suffer from TCP window latency and Telegram server-side throttling.
  * Measured chunk download speeds on DC 4 averaged **0.94 MB/s** (dropping as low as **0.2 MB/s** during peak hours, with single chunks taking up to 5.15s).
  * Measured chunk download speeds on DC 5 averaged **1.13 MB/s** (dropping as low as **0.1 MB/s**).
  * When wire delivery speed falls below 0.65 MB/s, the in-memory buffer runway of 24–32MB drains at a rate of 0.45 MB/s. Within 60 seconds of sustained congestion, buffer runway hits 0MB.
* **Forensic Evidence**:
  * In `The Threesome (2025)`: Runway hit 0.0MB on 28.6% of progress samples.
  * In `Fatal Seduction S01E07`: Runway hit 0.0MB on 100% of progress samples.
  * In `Celebrity Master Chef EP20`: Runway dropped to 2.0MB, forcing VLC to disconnect and retry with a new Range request.
* **Downstream Effects**:
  Audio/video stuttering, player freezing, and VLC issuing disconnects/reconnects.

---

### RC-006: Global `TelegramClient` Class Attribute Event Loop Invalidation

* **Formal Classification**: **CONFIRMED**
* **Affected Subsystems**: `backend/app/telegram_client.py:420-435`
* **Trigger Mechanism**:
  `TelegramClient` in Telethon is bound to the active asyncio event loop at the time `connect()` is called.
  In `telegram_client.py`, `cls._client` is stored as a class attribute on `TelegramClientWrapper`.
  When Uvicorn starts in development or reloads workers:
  1. The module is evaluated on Loop A.
  2. Uvicorn initializes the ASGI application on Loop B.
  3. When an incoming request calls `await cls.get_me()`, Telethon detects that its internal queues, transport sockets, and locks belong to Loop A:
     `The asyncio event loop must not change after connection`.
  4. Simultaneously, SQLite session database is locked because Loop A and Loop B both hold file descriptors on `tg_suite_user_session.session`.
* **Forensic Evidence**:
  * Exactly **50 occurrences** logged within 63 milliseconds between `19:33:19,977` and `19:33:20,040` on Nov 16 during Uvicorn server startup.
* **Downstream Effects**:
  Bootstrap failures and temporary 500 errors on authentication endpoints during server startup or reload.

---

### RC-007: Route Handler Task Stored in Place of Streaming Task in Connection Map

* **Formal Classification**: **CONFIRMED**
* **Affected Subsystems**: `backend/app/routes/proxy_streaming.py:619, 645, 650`
* **Trigger Mechanism**:
  In `proxy_streaming.py:619`:
  ```python
  curr_task = asyncio.current_task() # This is the FastAPI route handler task!
  ...
  _ACTIVE_STREAM_TASKS[session_key] = (curr_task, start) # Line 645
  ...
  async def stream_wrapper():
      handle.task = asyncio.current_task() # Line 650: THIS is the actual streaming task!
  ```
  1. The route handler function `handle_transmux_stream()` executes on `curr_task`.
  2. It creates `StreamingResponse(stream_wrapper(), ...)` and returns it to Starlette.
  3. `curr_task` immediately completes and enters the `DONE` state!
  4. Starlette/Uvicorn then spawns a separate background task to iterate over `stream_wrapper()`. That task is `handle.task`.
  5. When the next seek arrives, line 631 checks:
     `if old_t and not old_t.done() and old_t is not curr_task: old_t.cancel()`
     Because `old_t` was set to `curr_task` (which is already `done()`), `old_t.cancel()` is a **NO-OP**!
  6. The backend relies solely on `old_h.stop_event.set()`, leaving the actual streaming coroutine uncancelled until its next loop iteration.
* **Downstream Effects**:
  Streaming tasks take significantly longer to terminate after seeks, consuming memory and bandwidth while streaming chunks that the client has already abandoned.
