# FORENSIC EVIDENCE: RC-001 VLC PROBE STORM & ITERATOR CANCELLATION

**Target Subsystem**: `backend/app/routes/proxy_streaming.py:619-638`  
**Evidence Source**: `backend/data/tg_power_suite.log`  
**Sanitization Applied**: Channel IDs and Auth tokens masked.

---

## 1. MKV Demux Probing Sequence (Microsecond Trace)

The following sanitized log trace captures VLC opening `The Threesome (2025) DL ShowBoxMovieChannel.mkv` (File size: 1,014,640,746 Bytes) at `17:25:38` on `2026-11-17`.

```log
2026-11-17 17:25:38,903 [INFO] [proxy_streaming] STREAM [The Threesome (2025) DL ShowBoxMovieChannel.mkv] Incoming stream request from VLC/3.0.24: Range=bytes=0- (start=0, length=1,014,640,746B, total=1,014,640,746B)
2026-11-17 17:25:39,010 [INFO] [stream_engine] STREAM [The Threesome (2025) DL ShowBoxMovieChannel.mkv] Starting range: offset=0, length=1,014,640,746B (967.64MB), chunk_size=1024KB, total_chunks=968, DC=4, active_accounts=2

2026-11-17 17:25:48,091 [INFO] [proxy_streaming] STREAM [The Threesome (2025) DL ShowBoxMovieChannel.mkv] Incoming stream request from VLC/3.0.24: Range=bytes=1014614652- (start=1,014,614,652, length=26,094B, total=1,014,640,746B)
2026-11-17 17:25:48,092 [INFO] [stream_engine] STREAM [The Threesome (2025) DL ShowBoxMovieChannel.mkv] Starting range: offset=1,013,972,992, length=667,754B (0.64MB), chunk_size=1024KB, total_chunks=1, DC=4, active_accounts=2

2026-11-17 17:25:48,138 [INFO] [proxy_streaming] STREAM [The Threesome (2025) DL ShowBoxMovieChannel.mkv] Incoming stream request from VLC/3.0.24: Range=bytes=300- (start=300, length=1,014,640,446B, total=1,014,640,746B)
2026-11-17 17:25:48,139 [INFO] [stream_engine] STREAM [The Threesome (2025) DL ShowBoxMovieChannel.mkv] Starting range: offset=0, length=1,014,640,746B (967.64MB), chunk_size=1024KB, total_chunks=968, DC=4, active_accounts=2

2026-11-17 17:25:48,173 [INFO] [proxy_streaming] STREAM [The Threesome (2025) DL ShowBoxMovieChannel.mkv] Incoming stream request from VLC/3.0.24: Range=bytes=132- (start=132, length=1,014,640,614B, total=1,014,640,746B)
2026-11-17 17:25:48,174 [INFO] [stream_engine] STREAM [The Threesome (2025) DL ShowBoxMovieChannel.mkv] Starting range: offset=0, length=1,014,640,746B (967.64MB), chunk_size=1024KB, total_chunks=968, DC=4, active_accounts=2
```

### Analysis of Timing Gaps:
* Between Request 2 (`17:25:48,091`) and Request 3 (`17:25:48,138`): **47 milliseconds**.
* Between Request 3 (`17:25:48,138`) and Request 4 (`17:25:48,173`): **35 milliseconds**.
* Total elapsed time for 3 separate full-range requests: **82 milliseconds**.
* Because `proxy_streaming.py:627-632` cancels previous tasks immediately upon receiving any request from the same client, the task for Request 2 was aborted after 47ms, and Request 3 was aborted after 35ms, before MTProto could return any wire chunk data.

---

## 2. Demux Desynchronization & Re-Probe Loop in Master Chef EP21

Log trace from `2026-11-18 23:28:05` to `23:28:06`:

```log
2026-11-18 23:28:05,651 [INFO] [proxy_streaming] STREAM [ONLYHD4U Celebrity Master Chef (2026) S01 EP21 T.mkv] Incoming stream request from VLC/3.0.24: Range=bytes=0- (start=0, length=1,595,932,587B, total=1,595,932,587B)
2026-11-18 23:28:05,964 [INFO] [proxy_streaming] STREAM [ONLYHD4U Celebrity Master Chef (2026) S01 EP21 T.mkv] Incoming stream request from VLC/3.0.24: Range=bytes=1595910547- (start=1,595,910,547, length=22,040B, total=1,595,932,587B)
2026-11-18 23:28:06,120 [INFO] [proxy_streaming] STREAM [ONLYHD4U Celebrity Master Chef (2026) S01 EP21 T.mkv] Incoming stream request from VLC/3.0.24: Range=bytes=107- (start=107, length=1,595,932,480B, total=1,595,932,587B)
2026-11-18 23:28:06,364 [INFO] [proxy_streaming] STREAM [ONLYHD4U Celebrity Master Chef (2026) S01 EP21 T.mkv] Incoming stream request from VLC/3.0.24: Range=bytes=1595931922- (start=1,595,931,922, length=665B, total=1,595,932,587B)
2026-11-18 23:28:06,490 [INFO] [proxy_streaming] STREAM [ONLYHD4U Celebrity Master Chef (2026) S01 EP21 T.mkv] Incoming stream request from VLC/3.0.24: Range=bytes=121- (start=121, length=1,595,932,466B, total=1,595,932,587B)
2026-11-18 23:28:06,846 [INFO] [proxy_streaming] STREAM [ONLYHD4U Celebrity Master Chef (2026) S01 EP21 T.mkv] Incoming stream request from VLC/3.0.24: Range=bytes=1223895045- (start=1,223,895,045, length=372,037,542B, total=1,595,932,587B)
```

6 Range requests were received in **1.19 seconds**, causing 5 consecutive stream engine pipeline shutdowns and restarts in rapid succession.
