# TG POWER SUITE — STREAMING PERFORMANCE & QUANTITATIVE ANALYSIS

**Audit Window**: `2026-11-17 00:00:00 IST` to `2026-11-18 23:29:39 IST`  
**Host Environment**: Linux x86_64, Localhost Loopback (`127.0.0.1`), Fast Ethernet/Fiber Uplink (LAN IP `192.168.1.6`)  
**Data Sources**: Primary log parsing of 31,752 records, 788 VLC HTTP stream requests, 16,343 chunk transactions.

---

## 1. High-Level Workload Profile

During the two-day forensic audit period, TG Power Suite handled **788 HTTP streaming requests** from desktop VLC (`VLC/3.0.24`) across 9 high-definition video titles.

```
+-----------------------------------------------------------------------------------------------+
|                                    WORKLOAD COMPARISON TABLE                                  |
+--------------------------------------+-------------------+-------------------+----------------+
| Metric                               | Day 1 (Nov 17)    | Day 2 (Nov 18)    | Combined Total |
+--------------------------------------+-------------------+-------------------+----------------+
| Total HTTP Stream Requests           | 539               | 249               | 788            |
| - Header / Metadata Probes (<1MB)    | 7 (1.3%)          | 20 (8.0%)         | 27 (3.4%)      |
| - Timeline Seek Requests (>1MB)      | 532 (98.7%)       | 229 (92.0%)       | 761 (96.6%)    |
| Unique Video Titles Streamed         | 6                 | 4                 | 9*             |
| Total Chunks Delivered               | 8,260             | 8,083             | 16,343         |
| - RAM Buffer Cache Hits              | 926 (11.2%)       | 2,762 (34.2%)     | 3,688 (22.6%)  |
| - Disk Cache Slices Served           | 5,838 (70.7%)     | 337 (4.2%)        | 6,175 (37.8%)  |
| - MTProto Wire Chunks Fetched        | 1,496 (18.1%)     | 4,984 (61.7%)     | 6,480 (39.6%)  |
| Overall Cache Hit Ratio (RAM + Disk) | **81.9%**         | **38.3%**         | **60.4%**      |
| In-Flight Wire Deduplication Joins   | 314               | 195               | 509            |
| Stream Iterators Aborted Early       | 90                | 95                | 185            |
+--------------------------------------+-------------------+-------------------+----------------+
* One title (Celebrity Master Chef EP18) spanned midnight between Day 1 and Day 2.
```

### Operational Character Contrast:
* **Day 1 (Nov 17)** was characterized by **high-intensity timeline scrubbing and seeking** (532 seeks, 5,838 disk hits from repeated seeks within previously downloaded file segments). Over 40 seeks per minute were observed during peak intervals.
* **Day 2 (Nov 18)** was characterized by **long-duration continuous playback** of 40-minute Master Chef episodes (4,984 wire chunks, 2,762 RAM hits, low seek count), stressing steady-state wire throughput and buffer runway stability.

---

## 2. Latency & Time-To-First-Byte (TTFB) Analysis

TTFB measures the duration between FastAPI receiving the incoming HTTP Range request from VLC and the first byte of media data being yielded to the HTTP response stream.

```
+-------------------------------------------------------------------------------------------------------+
|                                        TTFB DISTRIBUTION TABLE                                        |
+-----------------------------+---------+-----------+-----------+-----------+------------+--------------+
| Request Category            | Samples | Min (sec) | p50 (sec) | p90 (sec) | p99 (sec)  | Max (sec)    |
+-----------------------------+---------+-----------+-----------+-----------+------------+--------------+
| RAM Cache Hit (Prefix/Head) | 3,688   | 0.000s    | 0.000s    | 0.001s    | 0.002s     | 0.005s       |
| Metadata Probes (<1MB)      | 27      | 0.050s    | 0.080s    | 0.420s    | 1.427s     | 1.427s       |
| Wire Media Stream Requests  | 336     | 0.040s    | 0.369s    | 1.726s    | 8.410s     | 45.374s*     |
+-----------------------------+---------+-----------+-----------+-----------+------------+--------------+
* Max TTFB occurred on an aborted request during DC 4 network socket timeout recovery.
```

### TTFB Insights:
1. **0ms RAM Prefix TTFB**: When a file had its initial 1MB head chunk pre-cached in RAM (`stream_buffer`), TTFB was effectively instantaneous ($< 1\text{ms}$). This provided instant playback start for sequential file openings.
2. **Metadata Probe Speed**: Head and tail probes bypassing the pipelined queue achieved a median TTFB of **80ms**, well within VLC's acceptable demuxer timeout window.
3. **Tail Latency During Seeks**: On cold seeks into un-cached file regions, TTFB degraded to **1.73s (p90)**. Under severe socket congestion on Telegram DC 4, TTFB spiked to **45.37s**, far exceeding VLC's default connection timeout and triggering client disconnects.

---

## 3. Telegram DC Wire Throughput & Bandwidth Breakdown

Chunk retrieval throughput was evaluated across all 6,480 MTProto wire chunk transfers over the two-day period.

```
+-----------------------------------------------------------------------------------------------+
|                                  TELEGRAM DC THROUGHPUT TABLE                                 |
+------+---------------+--------------+----------------+----------------+-----------------------+
| DC   | Location      | Total Chunks | Avg Rate (MB/s)| Min Rate (MB/s)| Peak Burst Rate (MB/s)|
+------+---------------+--------------+----------------+----------------+-----------------------+
| DC 4 | Europe/London | 1,506        | **0.94 MB/s**  | 0.00 MB/s*     | 3.40 MB/s             |
| DC 5 | Asia/SG       | 5,064        | **1.13 MB/s**  | 0.10 MB/s      | 7.00 MB/s             |
+------+---------------+--------------+----------------+----------------+-----------------------+
| ALL  | Aggregate     | 6,480        | **1.09 MB/s**  | 0.00 MB/s      | 7.00 MB/s             |
+------+---------------+--------------+----------------+----------------+-----------------------+
* 0.00 MB/s recorded when a chunk transfer timed out and required a retry.
```

### Throughput Dynamics:
* **DC 5 (Singapore)** provided significantly higher peak bandwidth (up to 7.0 MB/s) and a higher average (1.13 MB/s) than DC 4. Long-form video streaming from DC 5 (`Celebrity Master Chef`) enjoyed stable playback during off-peak hours.
* **DC 4 (Europe)** exhibited severe congestion during evening hours (17:00–18:30 IST), with average throughput falling below 1.0 MB/s and chunk download latencies stretching to **5.15 seconds per 1MB chunk**.
* **The Single-Client Python Bottleneck**: Because the Rust core was dormant (RC-003), Python Telethon was constrained by Python's single-threaded async event loop, unable to saturate the host's 100+ Mbps network interface.

---

## 4. Player Consumption Rate vs. Wire Rate & Buffer Runway

A streaming pipeline remains stable if and only if:
$$\text{Wire Throughput} \times (1 - \text{Packet Loss}) \ge \text{Player Consumption Bitrate}$$

When Wire Rate falls below Player Rate, the buffer runway ($R_{\text{buf}}$) drains according to:
$$\frac{dR_{\text{buf}}}{dt} = \text{Wire Rate} - \text{Player Rate}$$

```
+---------------------------------------------------------------------------------------------------------+
|                                    BUFFER RUNWAY DYNAMICS BY TITLE                                      |
+--------------------------+-------------+------------------+-----------------+--------------+------------+
| Media Title              | Video Size  | Player Rate MB/s | Wire Rate MB/s  | Avg Runway MB| Starvation |
+--------------------------+-------------+------------------+-----------------+--------------+------------+
| The Threesome (2025)     | 967.6 MB    | 0.38 MB/s (1.05) | 0.66 MB/s (2.2) | 11.7 MB      | **28.6%**  |
| Fatal Seduction S01E07   | 166.1 MB    | 0.69 MB/s (1.31) | 0.66 MB/s (2.3) | 0.0 MB       | **100.0%** |
| Stepdaddy (2026)         | 889.2 MB    | 3.42 MB/s (21.0) | 1.12 MB/s (3.4) | 28.8 MB      | **7.7%**   |
| Master Chef S01 EP18     | 1.53 GB     | 0.39 MB/s (4.01) | 1.29 MB/s (7.0) | 27.6 MB      | **0.0%**   |
| Master Chef S01 EP19     | 1.43 GB     | 0.28 MB/s (6.67) | 0.98 MB/s (6.8) | 23.7 MB      | **0.0%**   |
| Master Chef S01 EP20     | 1.45 GB     | 0.48 MB/s (4.31) | 0.95 MB/s (5.9) | 23.8 MB      | **0.0%**   |
| Master Chef S01 EP21     | 1.48 GB     | 2.00 MB/s (7.74) | 1.92 MB/s (6.7) | 20.2 MB      | **5.0%**   |
+--------------------------+-------------+------------------+-----------------+--------------+------------+
Note: Values in parentheses indicate peak burst rates observed during buffering or scrub catch-up.
```

### Key Performance Findings:
1. **Steady-State Stability**: In titles with low seek activity (`Master Chef EP18, EP19, EP20`), the pipeline achieved 0.0% buffer starvation. The lookahead buffer maintained a comfortable 23–28 MB in RAM (23–28 seconds of runway).
2. **Scrubbing Destabilization**: In `Stepdaddy (2026)` and `Master Chef EP21`, user scrubbing spikes caused VLC's consumption rate to surge to **7.7–21.0 MB/s** (as VLC consumed bytes rapidly to decode target frames). Wire rate could not keep pace, resulting in immediate buffer depletion and player underrun.
3. **Fatal Seduction Severe Starvation**: In `Fatal Seduction S01E07`, the 77-second seek storm prevented the buffer from ever filling beyond 1 chunk. Starvation was 100%, causing continuous player buffering spinners.

---

## 5. Cache Efficiency & Deduplication Analysis

```
+-----------------------------------------------------------------------------------------------+
|                                      CACHE TIER UTILIZATION                                   |
+-------------------+---------------------+-----------------------+-----------------------------+
| Cache Tier        | Deliveries Served   | Share of Total Traffic| Mean Latency to First Byte  |
+-------------------+---------------------+-----------------------+-----------------------------+
| RAM Cache         | 3,688 chunks        | 22.6%                 | < 0.001s (0ms)              |
| Disk Slices       | 6,175 slices        | 37.8%                 | 0.008s – 0.025s             |
| In-Flight Wire Join| 509 chunks         | 3.1%                  | 0.150s – 0.450s             |
| Cold Wire Fetch   | 5,971 chunks        | 36.5%                 | 0.350s – 5.150s             |
+-------------------+---------------------+-----------------------+-----------------------------+
```

### Cache Assessment:
* **The Dual-Layer Cache is Highly Effective**: 60.4% of all requested chunk data was served directly from local memory or NVMe disk without touching Telegram's servers.
* **Wire Deduplication Value**: The in-flight deduplication mechanism (`Joined in-flight wire download`) successfully saved **509 redundant wire transfers** during rapid scrubbing, protecting Telegram account rate limits from severe FloodWait penalties.
