# FORENSIC EVIDENCE: RC-005 BUFFER RUNWAY DEPLETION & WIRE DEFICIT

**Target Subsystem**: `backend/app/services/stream_engine.py` (Rate Governor & MTProto Transport)  
**Evidence Source**: Periodic 5-second progress telemetry in `backend/data/tg_power_suite.log`

---

## 1. Progress Telemetry Format

Stream progress is reported every 5 seconds by `stream_engine.py:974-977`:

```python
logger.info(
    f"STREAM [{clean_name}] Progress: {bytes_sent:,}/{length:,}B "
    f"({pct:.1f}%) | Player Rate: {playback_speed:.2f} MB/s | Buffer Runway: ~{buffered_mb:.1f}MB ({q_len} chunks in RAM) | {delivered_chunks} delivered"
)
```

---

## 2. Sample Starvation Trajectory: The Threesome (2025)

The trace below illustrates progressive buffer depletion when wire delivery falls below consumption rate:

```log
2026-11-17 17:26:01,942 [INFO] [stream_engine] STREAM [The Threesome (2025) DL ShowBoxMovieChannel.mkv] Progress: 1,048,576/1,005,978,462B (0.1%) | Player Rate: 0.10 MB/s | Buffer Runway: ~6.0MB (6 chunks in RAM) | 1 delivered
2026-11-17 17:26:07,055 [INFO] [stream_engine] STREAM [The Threesome (2025) DL ShowBoxMovieChannel.mkv] Progress: 4,194,304/1,005,978,462B (0.4%) | Player Rate: 0.28 MB/s | Buffer Runway: ~3.0MB (3 chunks in RAM) | 4 delivered
2026-11-17 17:26:12,180 [INFO] [stream_engine] STREAM [The Threesome (2025) DL ShowBoxMovieChannel.mkv] Progress: 6,291,456/1,005,978,462B (0.6%) | Player Rate: 0.35 MB/s | Buffer Runway: ~1.0MB (1 chunks in RAM) | 6 delivered
2026-11-17 17:26:17,290 [INFO] [stream_engine] STREAM [The Threesome (2025) DL ShowBoxMovieChannel.mkv] Progress: 8,388,608/1,005,978,462B (0.8%) | Player Rate: 0.39 MB/s | Buffer Runway: ~0.0MB (0 chunks in RAM) | 8 delivered
```

### Analysis:
* At `17:26:01`, runway was 6.0 MB.
* At `17:26:07`, runway dropped to 3.0 MB.
* At `17:26:12`, runway dropped to 1.0 MB.
* At `17:26:17`, runway dropped to **0.0 MB** (0 chunks in RAM).
* Telegram DC 4 was delivering chunks at 0.4 MB/s, while VLC was consuming at 0.39 MB/s with occasional decoder bursts.
* Once the buffer reached 0.0 MB, any minor network jitter immediately halted playback and caused VLC to stall.

---

## 3. Evening Congestion Episode: Master Chef EP20

During the evening session on `2026-11-18` between `22:42:06` and `23:20:53`:

```log
2026-11-18 23:20:43,620 [INFO] [stream_engine] STREAM [ONLYHD4U Celebrity Master Chef (2026) S01 EP20 T.mkv] Progress: 849,346,560/879,786,139B (96.5%) | Player Rate: 0.65 MB/s | Buffer Runway: ~2.0MB (2 chunks in RAM) | 810 delivered
2026-11-18 23:20:48,720 [INFO] [stream_engine] STREAM [ONLYHD4U Celebrity Master Chef (2026) S01 EP20 T.mkv] Progress: 852,492,800/879,786,139B (96.9%) | Player Rate: 0.65 MB/s | Buffer Runway: ~1.0MB (1 chunks in RAM) | 813 delivered
2026-11-18 23:20:53,825 [INFO] [proxy_streaming] STREAM [ONLYHD4U Celebrity Master Chef (2026) S01 EP20 T.mkv] Incoming stream request from VLC/3.0.24: Range=bytes=1447201489- (start=1,447,201,489, length=109,588,017B, total=1,556,789,506B)
```

Buffer runway dropped to 1.0 MB, at which point VLC disconnected and reopened with a new Range request to resume at offset 1,447,201,489.
