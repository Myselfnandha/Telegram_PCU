# TG POWER SUITE — STREAMING ARCHITECTURE & EXECUTION TRACE

**Audit Window**: `2026-11-17 00:00:00 IST` to `2026-11-18 23:29:39 IST`  
**Purpose**: Complete technical trace of control flow, concurrency models, task lifetimes, and inter-process communication across TG Power Suite's streaming stack.

---

## 1. End-to-End System Topology

```mermaid
flowchart TD
    Client["VLC Desktop / Web Browser"] -->|"HTTP GET /dl/{chat_id}/{msg_id}/{name}<br/>(Range: bytes=X-Y)"| FastAPIRoute["FastAPI Route Handler<br/>(proxy_streaming.py)"]
    
    FastAPIRoute -->|"1. Session / Deduplication Check"| ActiveTasks["_ACTIVE_STREAM_TASKS<br/>_ACTIVE_STREAM_ITERS"]
    FastAPIRoute -->|"2. Return StreamingResponse"| Uvicorn["Starlette / Uvicorn ASGI Runner"]
    
    Uvicorn -->|"Iterate Async Generator"| StreamWrapper["stream_wrapper() Coroutine"]
    StreamWrapper -->|"async for chunk in gen"| StreamEngine["StreamEngine.stream_media()<br/>(stream_engine.py)"]
    
    StreamEngine -->|"Check RAM Prefix / Cache"| StreamBuffer["StreamBuffer (RAM Cache)<br/>StreamCacheService (Disk Cache)"]
    StreamEngine -->|"Spawn Background Worker"| ProducerTask["pipelined_chunk_producer()<br/>(asyncio.Task)"]
    
    ProducerTask -->|"Check Status"| RustBridge["RustBridge<br/>(rust_bridge.py)"]
    RustBridge -.->|"HTTP POST :8099/health<br/>(active_accounts: 0 -> Fails)"| RustCore["tg-rust-core Daemon<br/>(Port 8099)"]
    
    ProducerTask -->|"Fallback to Python MTProto"| TelethonPool["Telethon MTProto Sender Pool<br/>(Primary + Secondary Senders)"]
    TelethonPool -->|"TCP / MTProto Layer 228"| TelegramDC["Telegram DC 4 / DC 5 Sockets"]
```

---

## 2. VLC Container Probing & Request Lifecycle

When VLC opens an MKV file over HTTP, it sends a burst of non-contiguous Range requests to parse the container before playing media frames.

```mermaid
sequenceDiagram
    autonumber
    actor VLC as VLC Desktop Player
    participant Proxy as FastAPI (proxy_streaming.py)
    participant Engine as StreamEngine (stream_engine.py)
    participant Cache as RAM / Disk Cache
    participant DC as Telegram MTProto DC

    Note over VLC,Proxy: Phase 1: Container Demux Discovery (< 300ms)
    VLC->>Proxy: GET /dl/... Range: bytes=0-
    Proxy->>Engine: stream_media(offset=0)
    Engine->>Cache: Check RAM prefix (0MB)
    Cache-->>Proxy: RAM Hit (1MB) -> Yield to VLC (0ms TTFB)
    
    VLC->>Proxy: GET /dl/... Range: bytes=<size-22KB>- (Tail Probe)
    Note over Proxy: Cancels prior task #1, advances seek gen!
    Proxy->>Engine: stream_media(offset=tail)
    Engine->>DC: GetFileRequest(tail_offset)
    DC-->>Proxy: 22KB Tail Chunk -> Yield to VLC
    
    VLC->>Proxy: GET /dl/... Range: bytes=107- (Segment Header)
    Note over Proxy: Cancels prior task #2, advances seek gen!
    Proxy->>Engine: stream_media(offset=107)
    Engine->>Cache: Slice 0..1MB (Disk/RAM)
    Cache-->>Proxy: Header Slice -> Yield to VLC

    VLC->>Proxy: GET /dl/... Range: bytes=<size-665B>- (Void Element)
    Note over Proxy: Cancels prior task #3, advances seek gen!

    Note over VLC,Proxy: Phase 2: Playback Stream Initiation
    VLC->>Proxy: GET /dl/... Range: bytes=<initial_seek_offset>-
    Note over Proxy: Cancels prior task #4, starts media pipeline
    Proxy->>Engine: stream_media(offset=seek_offset)
    Engine->>DC: Parallel MTProto Chunk Pipelining
    DC-->>Engine: 1MB Chunks
    Engine-->>Proxy: Yield 1MB Chunks
    Proxy-->>VLC: 206 Partial Content Stream
```

---

## 3. The Task Cancellation Race Condition (RC-002 & RC-004)

The diagram below details the exact execution race between `proxy_streaming.py`, `stream_engine.py`, and Python's `asyncio` runtime that produces **93 leaked pending tasks** and **16 `aclose()` runtime errors**.

```mermaid
sequenceDiagram
    autonumber
    actor VLC as VLC Player
    participant Route as Route Handler (curr_task)
    participant Wrapper as stream_wrapper()
    participant Engine as stream_media()
    participant Producer as pipelined_chunk_producer()
    participant DC as Telegram Socket

    VLC->>Route: Request N (Scrub Slider)
    Route->>Wrapper: Start Request N
    Wrapper->>Engine: async for chunk in gen:
    Engine->>Producer: asyncio.create_task(pipelined_chunk_producer())
    Producer->>DC: await _download_chunk_from_mtproto() (takes 3-5s)

    Note over VLC,Route: User scrubs slider again! (within 500ms)
    VLC->>Route: Request N+1
    Route->>Wrapper: old_t.cancel() (Cancels Request N)
    Route->>Wrapper: old_h.stop_event.set()

    Note over Wrapper: Wrapper receives CancelledError
    Wrapper->>Wrapper: enters finally: await gen.aclose()
    
    critical Generator Concurrency Conflict (RC-004)
        Wrapper->>Engine: await gen.aclose()
        Note over Engine: gen is currently executing an athrow/CancelledError!
        Engine-->>Wrapper: RuntimeError: aclose(): asynchronous generator is already running
    end

    Engine->>Engine: enters stream_media() finally:
    Engine->>Producer: producer_task.cancel()
    
    critical Producer Cancellation Timeout (RC-002)
        Engine->>Producer: await asyncio.wait_for(producer_task, timeout=0.8)
        Note over Producer: Producer is blocked in DC socket read (takes > 0.8s)
        Producer-->>Engine: Timeout after 800ms! (asyncio.TimeoutError)
        Note over Engine: Engine catches TimeoutError with pass!
        Engine-->>Wrapper: stream_media() exits and destroys generator object
        Note over Producer: ProducerTask is left running in background without references!
        Note over Producer: Garbage Collection runs -> [ERROR] Task was destroyed but it is pending!
    end
```

---

## 4. Rust Bridge Session Registration Breakdown (RC-003)

The architectural failure of the Rust core integration is illustrated below:

```mermaid
flowchart TD
    subgraph Config["Configuration Subsystem (config.py)"]
        EnvFile[".env / Environment"] -->|"TG_SESSION_STRING=''"| ConfigEnv["SESSION_STRING = ''"]
        SessionDir["backend/sessions/"] -->|"tg_suite_user_session.session"| SQLiteFile["SQLite Session DB (Active)"]
    end

    subgraph FastAPISub["FastAPI Health Poller (rust_bridge.py)"]
        PollLoop["Every 2 seconds:<br/>GET http://127.0.0.1:8099/health"]
        HealthCheck{"resp.json()<br/>active_accounts > 0?"}
        EnsureReg["ensure_sessions_registered()"]
        CheckString{"if SESSION_STRING and API_ID"}
        DeadEnd["NO-OP (Returns False)<br/>Never calls /api/session/register"]
    end

    subgraph RustDaemon["tg-rust-core Daemon (Port 8099)"]
        RustPort["Axum Server :8099"]
        RustHealth["/health -> active_accounts: 0"]
        RustGrammers["Grammers MTProto Core (Dormant)"]
    end

    subgraph StreamingPipeline["Stream Execution (stream_engine.py)"]
        ChunkReq["Fetch Chunk"]
        IsHealthy{"await rust_bridge.is_healthy()"}
        PythonFallback["Fallback to Python Telethon (1.1 MB/s)"]
        RustStream["rust_bridge.stream_chunk() (24 MB/s)"]
    end

    PollLoop --> RustPort
    RustPort --> RustHealth
    RustHealth --> HealthCheck
    HealthCheck -- "active_accounts == 0 (False)" --> EnsureReg
    EnsureReg --> CheckString
    ConfigEnv --> CheckString
    CheckString -- "SESSION_STRING is Empty" --> DeadEnd
    
    ChunkReq --> IsHealthy
    IsHealthy -- "Always False" --> PythonFallback
    IsHealthy -.->|"Never reached"| RustStream
```

### Key Concurrency Invariants & Findings:
1. **Lock Contention**: Telethon's internal sender pool lock (`MTProtoSender._lock`) serializes chunk fetches on the primary client when multiple probe requests arrive simultaneously.
2. **Buffer Queue Bound**: `queue: asyncio.Queue(maxsize=32)` limits in-memory chunks. When the consumer (`stream_media`) aborts, `queue.put(None)` at line 905 blocks if the producer has filled the queue, causing the producer to hang indefinitely until garbage collected.
3. **Session SQLite Lock**: Telethon's `SQLiteSession` maintains an open write connection. When Uvicorn restarts, multiple threads/coroutines contending for `tg_suite_user_session.session` encounter `sqlite3.OperationalError: database is locked`.
