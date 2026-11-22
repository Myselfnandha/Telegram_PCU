# TG POWER SUITE — STREAMING LOG INVENTORY & AUDIT METADATA

**Audit Period**: `2026-11-17 00:00:00 IST` to `2026-11-18 23:29:39 IST`  
**Host Timezone**: `Asia/Kolkata (IST, UTC+05:30)`  
**Audit Scope**: Complete available log repository across consecutive operational days.

---

## 1. Primary Log File Inventory

| File Path | Size (Bytes) | Line Count | Earliest Timestamp (IST) | Latest Timestamp (IST) | Status & Role |
|---|---|---|---|---|---|
| `backend/data/tg_power_suite.log` | 5,207,939 | 33,563 | `2026-11-16 19:32:47,885` | `2026-11-18 23:29:39,555` | **Active Primary Log**. Covers entire Day 1 (Nov 17) and Day 2 (Nov 18), plus Nov 16 startup. |
| `backend/data/tg_power_suite.log.1` | 10,485,654 | 76,920 | `2026-11-12 21:57:07,515` | `2026-11-16 19:32:47,881` | **Rotated Historical Log**. Seamless rotation boundary at `19:32:47,881`. Contains past Rust bridge circuit trips and 504 timeouts. |
| `backend/data/tg_power_suite.log.2` | 10,485,739 | 73,412 | `2026-11-05 18:56:38,000` | `2026-11-12 21:57:07,510` | **Archived Log**. Historical multi-chunk and bad gateway benchmarks. |
| `backend/data/vlc_desktop.log` | 58,528 | 1,027 | N/A (un-timestamped) | N/A | **Client Log**. Desktop VLC stdout/stderr capture containing MKV demux errors, audio output errors, and H.264 reference frame warnings. |

---

## 2. Temporal Window & Clock Analysis

### 2.1 Timezone Definition
* **System Local Time**: `Asia/Kolkata (IST)` = `UTC + 05:30`.
* **Log Formats**: Python standard `logging` format `%Y-%m-%d %H:%M:%S,%f`. All timestamps logged by FastAPI, Telethon, Uvicorn, and background tasks reflect local system time (`IST`).
* **Audit Time Window**:
  * **Window Start**: `2026-11-17 00:00:00,000 IST` (`2026-11-16 18:30:00,000 UTC`)
  * **Window End**: `2026-11-18 23:29:39,555 IST` (`2026-11-18 17:59:39,555 UTC`)
  * **Pre-Window Context**: `2026-11-16 19:32:47 IST` to `23:59:59 IST` (retained for server bootstrap, database lock, and event loop initialization audit).

### 2.2 Continuity Across File Rotation
A critical check was performed on the boundary between `tg_power_suite.log.1` and `tg_power_suite.log`:
* `tg_power_suite.log.1` final entry: `2026-11-16 19:32:47,881 [INFO] [httpx] HTTP Request: GET http://127.0.0.1:8099/health "HTTP/1.1 200 OK"`
* `tg_power_suite.log` first entry: `2026-11-16 19:32:47,885 [INFO] [proxy_streaming] Registering streaming routes...`
* **Continuity verdict**: **100% SEAMLESS**. Gap between files is exactly 4 milliseconds. No log records were lost or overwritten during log rotation.

### 2.3 Clock Skew & Inter-Component Synchronization
* **FastAPI Backend (PID 2424)**: Runs on the host OS event loop. All log records share the single monotonic and wall-clock reference of the host Linux kernel.
* **Rust Core (PID 2422)**: Monitored over localhost loopback HTTP (`127.0.0.1:8099`). Round-trip latency across the loopback interface is $< 0.4\text{ms}$. No clock drift exists between the two processes.
* **VLC Desktop (PID 20155)**: Localhost client connecting to `127.0.0.1:8088`. Requests arrive directly on local loopback sockets with negligible propagation delay.

---

## 3. Log Volume & Level Distribution (Two-Day Window)

Within the declared audit window (`2026-11-17 00:00:00` to `2026-11-18 23:29:39 IST`):

| Metric | Day 1 (`2026-11-17`) | Day 2 (`2026-11-18`) | Combined Total |
|---|---|---|---|
| **Total Log Lines** | 14,143 | 17,609 | **31,752** |
| `INFO` Level Lines | 13,998 | 17,605 | 31,603 |
| `WARNING` Level Lines | 2 | 1 | 3 |
| `ERROR` Level Lines | 143 | 3 | 146 |
| `DEBUG` Level Lines | 0 (suppressed) | 0 (suppressed) | 0 |
| **Stream Requests (VLC)** | 539 | 249 | **788** |
| **Wire Chunks Delivered** | 1,496 | 4,984 | **6,480** |
| **RAM Cache Hits** | 926 | 2,762 | **3,688** |
| **Disk Cache Hits** | 5,838 | 337 | **6,175** |
| **In-Flight Wire Joins** | 314 | 195 | **509** |

---

## 4. Missing Evidence & Telemetry Gaps

1. **Rust Core Subprocess Stdout/Stderr Redirection**:
   * In `backend/app/services/rust_bridge.py:103-104`, the Rust binary `tg-rust-core` is spawned via `subprocess.Popen(..., stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)`.
   * *Impact*: Any internal Rust panic, grammers network warning, or thread pool exhaustion is completely discarded and cannot be inspected in post-mortem analysis.
2. **VLC HTTP Client Request Headers**:
   * VLC logs in `vlc_desktop.log` lack millisecond-precision timestamps and do not log outgoing HTTP request headers or received HTTP response status codes. All client-side Range request telemetry had to be reconstructed from server-side FastAPI access logs.
3. **Frontend Browser MSE Playback Logs**:
   * Browser HTML5 video events (`waiting`, `stalled`, `seeking`, `timeupdate`, `error`) are executed on the client browser and are not forwarded to backend logging.
