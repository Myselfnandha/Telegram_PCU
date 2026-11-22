# TG POWER SUITE — FINAL EXTENDED REAL-MEDIA SOAK TEST REPORT

**Execution Date**: November 19, 2026  
**Auditor**: Principal Streaming Systems Architect & SRE Team  
**Target Video**: `ONLYHD4U Celebrity Master Chef (2026) S01 EP21 T.mkv`  
**Media Metadata**: Chat ID `6161427514` | Message ID `14847` | Message Metadata Size: `1,595,932,587 bytes` | Telegram File Object Size: `1,083,742,643 bytes` | Container: `Matroska (MKV)`  

> [!WARNING]
> The disparity between message metadata size and streamed file object size is currently **unresolved**. No evidence indicates this is an engine defect.

---

## 1. Executive Summary

TG Power Suite was subjected to an extended real-media soak test. All operations completed with **zero playback stalls, zero deadlocks, zero task leaks, and zero generator errors**.

---

## 2. Test Execution & Quantitative Telemetry

### 2.1 Sustained VLC Continuous Playback
- **Duration**: >20 minutes.
- **Buffer Runway**: Consistently pegged at 20.0 MB to 24.0 MB (20 to 24 chunks).
- **Wire Delivery Source**: 100% Rust MTProto core.
- **Stalls**: 0.

### 2.2 Seek Performance Metrics

| Seek Type | Target | Mean Latency | Observed Range |
|---|---|---|---|
| **Cold Wire Forward Seeks** | 10 seeks (0–600MB) | 0.826s | 0.555 – 1.285s |
| **Cached RAM Backward Seeks**| 10 seeks (600MB–0) | 0.101s | 0.085 – 0.134s |
| **Rapid Scrub Seeks** | 20 burst seeks | 0.359s (TTFB) | 0.259 – 0.654s |

---

### 2.3 10 Cold-Cache Random Seeks (Distributed Sampling)
Ten random offsets were selected across the full 1.08 GB file range to verify wire performance under cold conditions.

| Seek Index | Random Byte Offset | Status | Response Latency |
|---|---|---|---|
| Seek 1 | 50,590,482 | 206 | 1.898s |
| Seek 2 | 1,068,777,503 | 206 | 0.372s |
| Seek 3 | 691,015,580 | 206 | 0.427s |
| Seek 4 | 668,683,852 | 206 | 0.789s |
| Seek 5 | 187,153,538 | 206 | 1.918s |
| Seek 6 | 174,501,937 | 206 | 1.641s |
| Seek 7 | 869,780,766 | 206 | 0.364s |
| Seek 8 | 484,657,450 | 206 | 1.641s |
| Seek 9 | 1,043,039,793 | 206 | 0.384s |
| Seek 10| 267,534,543 | 206 | 1.142s |

- **Cold Seek Latency Profile**:
  - **Median**: 0.966s
  - **Maximum Observed**: 1.918s

---

## 3. Browser MSE & Player Coverage
- **Browser MSE**: Validated for 15.00 MB transmuxed fMP4 transfer integrity (1.97 MB/s). 
- **Player Coverage**: Validated for VLC and Browser MSE. 

---

## 4. Stability
All resource consumption metrics (Backend, Rust Core, VLC RSS) remained stable.
