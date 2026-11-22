# FORENSIC EVIDENCE: RC-002 TASK DESTROYED WHILE PENDING (ASYNCIO LEAK)

**Target Subsystem**: `backend/app/services/stream_engine.py:1039-1044`  
**Evidence Source**: `backend/data/tg_power_suite.log`  
**Total Incidents**: 93 occurrences between `18:04:24` and `18:12:36` on `2026-11-17`.

---

## 1. Initial Failure Episode & DC 4 Network Glitch

Log trace showing the first occurrence at `18:04:24` during `Stepdaddy (2026)` playback:

```log
2026-11-17 18:04:24,248 [ERROR] [asyncio] Task was destroyed but it is pending!
task: <Task pending name='Task-8979' coro=<StreamEngine.stream_media.<locals>.pipelined_chunk_producer() running at /home/nandha/Desktop/TG_TOOL/backend/app/services/stream_engine.py:905> wait_for=<Future pending cb=[Task.task_wakeup()]>>
2026-11-17 18:04:24,261 [INFO] [stream_engine] STREAM [Stepdaddy (2026) Tagalog BONE.mkv] Chunk at 115,343,360: 1,048,576B in 5.15s (0.2 MB/s) from DC 4
2026-11-17 18:04:24,270 [INFO] [httpx] HTTP Request: GET http://127.0.0.1:8099/health "HTTP/1.1 200 OK"
2026-11-17 18:04:24,892 [INFO] [stream_engine] STREAM [Stepdaddy (2026) Tagalog BONE.mkv] Chunk at 116,391,936: 1,048,576B in 0.60s (1.7 MB/s) from DC 4
2026-11-17 18:04:30,373 [INFO] [proxy_streaming] STREAM [Stepdaddy (2026) Tagalog BONE.mkv] Incoming stream request from VLC/3.0.24: Range=bytes=31728287- (start=31,728,287, length=900,686,739B, total=932,415,026B)
2026-11-17 18:04:35,065 [WARNING] [stream_engine] STREAM [Stepdaddy (2026) Tagalog BONE.mkv] Network/socket glitch on DC 4 (attempt 1/6): TimeoutError
```

### Forensic Analysis of the Correlation:
1. At `18:04:24,261`, the log reports that chunk `115,343,360` took **5.15 seconds** to download from DC 4 (down to 0.2 MB/s).
2. Exactly 13 milliseconds prior, at `18:04:24,248`, the asyncio runtime emitted `Task was destroyed but it is pending!`.
3. In `stream_engine.py:1042`:
   ```python
   producer_task.cancel()
   try:
       await asyncio.wait_for(producer_task, timeout=0.8)
   except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
       pass
   ```
4. Because the chunk download on DC 4 took 5.15 seconds, the cancellation of `pipelined_chunk_producer` could not complete within the 0.8-second timeout.
5. `asyncio.wait_for` timed out, caught the error with `pass`, and allowed `stream_media` to exit.
6. The `producer_task` was left running orphaned in the event loop, waiting on line 905 (`await queue.put(None)`), until garbage collected.

---

## 2. Cascade of 92 Additional Leaked Tasks

The table below shows the distribution of the 93 leaked tasks over the 8-minute congestion window:

| Timestamp Window (IST) | Leaked Tasks Count | Triggering Action |
|---|---|---|
| `18:04:24` | 1 | DC 4 5.15s socket delay on offset 115MB |
| `18:06:49` – `18:07:19` | 4 | Timeline scrub to offset 76MB |
| `18:08:03` – `18:08:45` | 10 | Rapid scrubbing between 110MB and 140MB |
| `18:09:49` – `18:10:44` | 10 | Rapid scrubbing around offset 177MB |
| `18:11:11` – `18:11:32` | 8 | Scrubbing around offset 250MB |
| `18:12:36` – `18:23:39` | 60 | Continued scrubbing through 500MB–800MB |
| **TOTAL** | **93** | **100% correlated with stream seeks during DC 4 delays** |
