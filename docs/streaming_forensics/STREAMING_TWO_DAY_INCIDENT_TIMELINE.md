# TG POWER SUITE — TWO-DAY STREAMING INCIDENT TIMELINE

**Audit Window**: `2026-11-17 00:00:00 IST` to `2026-11-18 23:29:39 IST`  
**Pre-Window Context**: `2026-11-16 19:32:47 IST` to `23:59:59 IST`  
**Time Reference**: All timestamps are in Indian Standard Time (`IST`, `UTC+05:30`).

---

## 1. Overview of Forensic Sessions

```
+---------------------------------------------------------------------------------------------------------+
|                                    TWO-DAY TIMELINE AT A GLANCE                                         |
+---------------------+-----------------------+--------------------+----------------+---------------------+
| Session Date / Time | Media File Name       | VLC HTTP Reqs      | Wire Chunks    | Primary Anomaly     |
+---------------------+-----------------------+--------------------+----------------+---------------------+
| Nov 16 19:33:08 IST | (System Startup)      | N/A                | N/A            | Event loop/DB locks |
| Nov 17 17:14:58 IST | (Network Disconnect)  | N/A                | N/A            | Carrier down alert  |
| Nov 17 17:25:38 IST | The Threesome (2025)  | 67 requests        | 506 chunks     | aclose() collisions |
| Nov 17 17:41:50 IST | Fatal Seduction S01E7 | 71 requests        | 83 chunks      | 77s seek storm      |
| Nov 17 17:51:40 IST | Stepdaddy (2026)      | 391 requests       | 7,137 chunks   | 93 leaked tasks     |
| Nov 17 23:58:43 IST | Master Chef S01 EP18  | 25 requests        | 2,054 chunks   | Long steady stream  |
| Nov 18 12:24:14 IST | Master Chef S01 EP20  | 82 requests        | 2,719 chunks   | 43 stream aborts    |
| Nov 18 12:27:55 IST | Master Chef S01 EP19  | 16 requests        | 1,895 chunks   | Low seek, stable    |
| Nov 18 23:23:00 IST | Master Chef S01 EP21  | 131 requests       | 1,474 chunks   | Scrub loop + re-probe|
+---------------------+-----------------------+--------------------+----------------+---------------------+
```

---

## 2. Chronological Incident Reconstruction

### Incident 0: Uvicorn Startup Event Loop Conflict & Database Lock
* **Time**: `2026-11-16 19:33:08,911` to `19:33:20,040 IST` (Lines 113–252)
* **Target**: FastAPI Application Bootstrap / Telethon SQLite Session
* **Event Sequence**:
  1. `19:33:08,911` [L113]: Uvicorn worker starts and attempts to open `SESSION_DIR / "tg_suite_user_session.session"`.
  2. Telethon emits: `[WARNING] [telegram_client] MTProto connect error (network may be switching): database is locked`.
  3. `19:33:19,382` [L161]: Profile retrieval fails with `database is locked`.
  4. `19:33:19,977` to `19:33:20,040` [L180-L252]: 50 consecutive calls to `cls.get_me()` fail in rapid succession with:
     `[ERROR] [telegram_client] Error getting Telegram profile: The asyncio event loop must not change after connection (see https://docs.telethon.dev/en/stable/quick-references/faq.html#the-asyncio-event-loop-must-not-change-after-connection)`.
* **Root Cause**: The global singleton `TelegramClient` was created and bound to an initial asyncio loop before Uvicorn reloaded or created its worker event loop.
* **Resolution in Log**: Subsequent initialization at `19:44:03` cleanly initialized `cls._client`, establishing persistent connection for subsequent days.

---

### Incident 1: Network Interface Drop & Reconnect
* **Time**: `2026-11-17 17:14:58,087` to `17:15:04,816 IST` (Lines 1771–1774)
* **Target**: Host Physical Network Interface
* **Event Sequence**:
  1. `17:14:58,087` [L1771]: `[WARNING] [network_monitor] NetworkMonitor: Network interface disconnected / carrier down. Freezing in-flight transfers and purging stale MTProto sockets`.
  2. `17:14:58,091` [L1772]: Queue manager freezes batch transfers.
  3. `17:15:04,815` [L1773]: `[INFO] [network_monitor] NetworkMonitor: Network interface reconnected (LAN IP: 192.168.1.6). Reconnecting Telegram client...`
  4. Network recovered in 6.7 seconds. No active streams were running during this outage.

---

### Incident 2: The Threesome (2025) — Async Generator `aclose()` Collision
* **Time**: `2026-11-17 17:25:38,903` to `17:29:51,720 IST` (Lines 2074–3880)
* **File Details**: `The Threesome (2025) DL ShowBoxMovieChannel.mkv`, Size: 1,014,640,746 Bytes (967.6 MB)
* **Metrics**: 67 VLC HTTP Requests, 506 Chunks, 31 Aborts, Avg Wire Rate: 0.66 MB/s (DC 4).
* **Detailed Event Breakdown**:
  * `17:25:38,903` [L2074]: VLC opens file with initial probe `Range: bytes=0-`.
  * `17:25:48,091` [L2105]: VLC seeks to file tail `Range: bytes=1014614652-` (len=26,094B) to read MKV SeekHead/Cues.
  * `17:25:48,138` [L2112]: VLC requests `bytes=300-` (len=1,014,640,446B).
  * `17:25:48,173` [L2120]: VLC requests `bytes=132-` (len=1,014,640,614B) — 3 requests in 82 milliseconds!
  * `17:28:56,254` [L3700]: **First Generator Crash**:
    ```
    [ERROR] [asyncio] Task exception was never retrieved
    future: <Task finished name='Task-3703' coro=<<async_generator_athrow without __name__>()> exception=RuntimeError('aclose(): asynchronous generator is already running')>
    RuntimeError: aclose(): asynchronous generator is already running
    ```
  * `17:29:12,224` [L3872]: **Second Generator Crash**: Identical `RuntimeError: aclose(): asynchronous generator is already running` on `Task-3875`.
* **Root Cause Analysis**: At `17:28:56`, the user scrubbed to offset `315,621,376`. A new HTTP request arrived while the previous request's `stream_wrapper` generator was suspended inside an `async for chunk in gen:` yield. The previous task was cancelled by `old_t.cancel()`, entering `finally:` where `await gen.aclose()` was called concurrently on a generator that was already executing an athrow/cancellation unwinding step.

---

### Incident 3: Fatal Seduction S01E07 — High-Frequency 77-Second Seek Storm
* **Time**: `2026-11-17 17:41:50,629` to `17:43:07,658 IST` (Lines 4180–4350)
* **File Details**: `Fatal Seduction S01E07 NF GalaxyTV.mkv`, Size: 174,131,046 Bytes (166.1 MB)
* **Metrics**: 71 VLC HTTP Requests in 77 seconds (0.92 req/sec), 83 Chunks Delivered, 31 Aborts.
* **Detailed Event Breakdown**:
  * User dragged the VLC playback slider repeatedly back and forth across the entire 174MB video.
  * `17:41:50` to `17:41:54`: 4 initial probe requests (`bytes=0-`, tail `173806637-`, header `219-`, header `124-`).
  * `17:42:00` to `17:43:02`: 67 rapid Range seek requests issued at 0.1s to 1.5s intervals.
  * Wire deduplication joined in-flight chunk requests:
    `Chunk 3/139 at 31,457,280: Joined in-flight wire download (request=req_03801bf1, episode=ep_419d2a51, subscribers=1)`
  * `17:43:00,992` [L4320]: Third occurrence of `RuntimeError: aclose(): asynchronous generator is already running` on `Task-6689`.
  * Buffer runway remained at 0.0 MB throughout the 77-second storm because requests were cancelled before more than 1 chunk could be buffered into RAM.

---

### Incident 4: Stepdaddy (2026) — 391 Requests & 93 Leaked Pending Tasks
* **Time**: `2026-11-17 17:51:40,786` to `18:23:39,814 IST` (Lines 4416–15620)
* **File Details**: `Stepdaddy (2026) Tagalog BONE.mkv`, Size: 932,415,026 Bytes (889.2 MB)
* **Metrics**: **391 VLC HTTP Requests**, **7,137 Chunks**, 25 Aborts, 5,838 Disk Cache Hits, 93 Orphaned Leaked Tasks.
* **Detailed Event Breakdown**:
  * `17:51:40` to `17:52:03`: File opened, tail probed (`932382263-`), initial playback starts.
  * `18:04:18` to `18:04:35`: Playback progresses to offset 31,728,287.
  * `18:04:24,248` [L4801]: **First Orphaned Task Leak**:
    ```
    [ERROR] [asyncio] Task was destroyed but it is pending!
    task: <Task pending name='Task-8979' coro=<StreamEngine.stream_media.<locals>.pipelined_chunk_producer() running at /home/nandha/Desktop/TG_TOOL/backend/app/services/stream_engine.py:905> wait_for=<Future pending cb=[Task.task_wakeup()]>>
    ```
  * `18:04:24,261` [L4802]: Chunk at 115,343,360 took **5.15 seconds** to download from Telegram DC 4 (0.2 MB/s).
  * `18:04:35,065` [L4820]: `[WARNING] [stream_engine] Network/socket glitch on DC 4 (attempt 1/6): TimeoutError`.
  * `18:06:49` to `18:12:36` [L5390–L7500]: **Cascade of 92 Additional Orphaned Task Leaks**:
    As user scrubbed through the file while DC 4 network latency was fluctuating, VLC issued new Range requests. Each new request cancelled `producer_task`. Because DC 4 chunk downloads were taking >1s, `await asyncio.wait_for(producer_task, timeout=0.8)` timed out after 800ms.
    The generator terminated, leaving the active `pipelined_chunk_producer()` tasks running without any consumer reference. When GC ran, 93 pending tasks were destroyed.
  * `18:23:37` to `18:23:39` [L15562–L15618]: **Rapid 14-Request Scrub Burst**:
    VLC sent 14 Range requests in 2.6 seconds around offset 804MB (86% into video) before user halted playback.

---

### Incident 5: Celebrity Master Chef EP20 — Midday & Evening Playback with Buffer Starvation
* **Time**: `2026-11-18 12:24:14,751` to `23:22:56,092 IST` (Lines 20147–29800)
* **File Details**: `ONLYHD4U Celebrity Master Chef (2026) S01 EP20 T.mkv`, Size: 1,556,789,506 Bytes (1.45 GB)
* **Metrics**: 82 VLC HTTP Requests, 2,719 Chunks Delivered, 43 Stream Aborts, Avg Wire Rate: 0.95 MB/s (DC 5).
* **Detailed Event Breakdown**:
  * Split across 3 viewing sessions: Midday (12:24–12:27), Afternoon (15:05–15:41), and Late Evening (22:42–23:22).
  * During the evening session (`22:42:06` to `23:20:53`), player consumed data at 0.48–1.2 MB/s.
  * Wire download speed fluctuated between 0.1 MB/s and 0.9 MB/s on Telegram DC 5.
  * Buffer runway repeatedly shrank from 24MB down to 2.0MB.
  * At `23:20:53`, VLC disconnected and re-opened the stream with Range `bytes=1447201489-` (offset 1.34 GB) to recover audio/video synchronization after a buffer underrun.

---

### Incident 6: Celebrity Master Chef EP21 — Scrub Loop, Full-File Re-probe & Premature Disconnects
* **Time**: `2026-11-18 23:23:00,216` to `23:33:37,820 IST` (Lines 29928–33560)
* **File Details**: `ONLYHD4U Celebrity Master Chef (2026) S01 EP21 T.mkv`, Size: 1,595,932,587 Bytes (1.48 GB)
* **Metrics**: 131 VLC HTTP Requests, 1,474 Chunks Delivered, 48 Stream Aborts, 4 Client Disconnects.
* **Detailed Event Breakdown**:
  * `23:23:00` [L29928]: Initial file open `Range: bytes=0-`.
  * `23:27:59,205` to `23:28:00,800` [L32972–L33168]: **Rapid Scrubbing Burst**:
    VLC sent 7 Range requests in 1.6 seconds exploring timestamps around 69% of the file:
    `849604065-` $\to$ `1104977025-` $\to$ `1105871728-` $\to$ `1107761213-` $\to$ `1108937625-` $\to$ `1109958946-` $\to$ `1111047907-`.
  * `23:28:05,651` [L33174]: **Demuxer Desync & Complete File Re-probe**:
    Because data for the scrubbed offsets was not yet buffered in RAM, VLC's demuxer failed to find keyframes and initiated a complete file re-probe sequence:
    1. `23:28:05,651`: `Range: bytes=0-` (EBML header)
    2. `23:28:05,964`: `Range: bytes=1595910547-` (tail, 22KB SeekHead)
    3. `23:28:06,120`: `Range: bytes=107-` (Segment element)
    4. `23:28:06,364`: `Range: bytes=1595931922-` (tail, 665B Cues)
    5. `23:28:06,490`: `Range: bytes=121-` (Tracks element)
    6. `23:28:06,846`: `Range: bytes=1223895045-` (Target seek position, 76.7%)
  * `23:28:12,933` [L33432]: VLC reopened playback at `bytes=20464758-`.
  * Active VLC PID 20155 is currently streaming this exact media file.
