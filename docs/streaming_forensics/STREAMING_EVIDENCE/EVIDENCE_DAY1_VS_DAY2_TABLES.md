# FORENSIC EVIDENCE: QUANTITATIVE DAY 1 VS DAY 2 COMPARISON

**Audit Window**: `2026-11-17 00:00:00 IST` to `2026-11-18 23:29:39 IST`  
**Dataset**: Full programmatic parse of `backend/data/tg_power_suite.log`

---

## 1. Daily Aggregated Telemetry Matrix

```
+-----------------------------------------------------------------------------------------------+
| METRIC                                | DAY 1 (2026-11-17)    | DAY 2 (2026-11-18)    | DELTA |
+-----------------------------------------------------------------------------------------------+
| Total Log Entries                     | 14,143                | 17,609                | +24.5%|
| Total HTTP Stream Requests            | 539                   | 249                   | -53.8%|
| - Header / Discovery Probes (<1MB)    | 7                     | 20                    | +185% |
| - Timeline Seeks / Stream Ranges      | 532                   | 229                   | -57.0%|
| Unique Media Titles Streamed          | 6                     | 4                     | -33.3%|
| Total MTProto Wire Chunks Delivered   | 1,496                 | 4,984                 | +233% |
| RAM Buffer Hits (0ms TTFB)            | 926                   | 2,762                 | +198% |
| Disk Cache Slices Served              | 5,838                 | 337                   | -94.2%|
| In-Flight Wire Deduplication Joins    | 314                   | 195                   | -37.9%|
| Leaked "Task Destroyed" Errors        | 93                    | 0                     | -100% |
| Generator aclose() Collisions         | 16                    | 0                     | -100% |
| Network Socket Timeouts / Glitches    | 1                     | 0                     | -100% |
| Stream Iterators Aborted Early        | 90                    | 95                    | +5.6% |
| Rust Core Health Inquiries (/health)  | 1,920                 | 1,902                 | -0.9% |
| Rust Core Chunks Dispatched           | 0                     | 0                     | 0.0%  |
+-----------------------------------------------------------------------------------------------+
```

---

## 2. Media Title Performance Comparison

```
+--------------------------------------------------------------------------------------------------------------+
| TITLE                                   | DATE    | REQS | CHUNKS | WIRE MB/s (AVG/MAX) | RUNWAY (MIN/AVG)  |
+--------------------------------------------------------------------------------------------------------------+
| The Threesome (2025)                    | Nov 17  | 67   | 506    | 0.66 / 2.20 MB/s    | 0.0 / 11.7 MB     |
| Fatal Seduction S01E01                  | Nov 17  | 1    | 2      | 0.30 / 0.40 MB/s    | N/A               |
| Fatal Seduction S01E07                  | Nov 17  | 71   | 83     | 0.66 / 2.30 MB/s    | 0.0 / 0.0 MB      |
| 24 02 Strip Tease                       | Nov 17  | 4    | 72     | 0.47 / 1.10 MB/s    | 0.0 / 3.0 MB      |
| Stepdaddy (2026)                        | Nov 17  | 391  | 7,137  | 1.12 / 3.40 MB/s    | 0.0 / 28.8 MB     |
| Celebrity Master Chef EP18              | Nov 17* | 25   | 2,054  | 1.29 / 7.00 MB/s    | 3.0 / 27.6 MB     |
| Celebrity Master Chef EP20              | Nov 18  | 82   | 2,719  | 0.95 / 5.90 MB/s    | 2.0 / 23.8 MB     |
| Celebrity Master Chef EP19              | Nov 18  | 16   | 1,895  | 0.98 / 6.80 MB/s    | 4.0 / 23.7 MB     |
| Celebrity Master Chef EP21              | Nov 18  | 131  | 1,474  | 1.92 / 6.70 MB/s    | 1.0 / 20.2 MB     |
+--------------------------------------------------------------------------------------------------------------+
* Master Chef EP18 started at 23:58:43 on Nov 17 and continued into the morning of Nov 18.
```

---

## 3. Data Center Latency & Throughput Distribution

```
+----------------------------------------------------------------------------------------------------+
| DATA CENTER | CONTINENT  | SAMPLES | MEAN (MB/s) | MEDIAN (MB/s) | p90 (MB/s)  | MAX (MB/s)        |
+----------------------------------------------------------------------------------------------------+
| DC 4        | Europe     | 1,506   | 0.94 MB/s   | 0.80 MB/s     | 1.70 MB/s   | 3.40 MB/s         |
| DC 5        | Asia       | 5,064   | 1.13 MB/s   | 1.00 MB/s     | 2.40 MB/s   | 7.00 MB/s         |
+----------------------------------------------------------------------------------------------------+
```
