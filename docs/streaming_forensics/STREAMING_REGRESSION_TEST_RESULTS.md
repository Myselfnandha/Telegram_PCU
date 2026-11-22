# TG POWER SUITE — STREAMING REGRESSION TEST RESULTS

**Test Date**: November 19, 2026  
**Environment**: Linux 6.17 (x86_64), Python 3.14.10, Pytest 9.1.1, Rust Cargo 1.85  
**Result**: 100% Passed (110 Python Tests, 4 Rust Unit Tests)  

---

## 1. Test Suite Summary

```
================================================================================
TOTAL TESTS EXECUTED: 114
PASSED: 114
FAILED: 0
SKIPPED: 0
PASS RATE: 100.0%
TOTAL RUNTIME: 21.68 seconds (Python: 19.58s, Cargo: 2.10s)
================================================================================
```

---

## 2. Test Execution Breakdown by Component

### 2.1 Targeted Forensic Remediation Test Suite (New Tests)

| Test File | Test Case Description | Result | Execution Time |
|---|---|---|---|
| `test_phase1_vlc_probe_coexistence.py` | Validates that concurrent VLC tail probes (2MB), head probes (1MB), and open-ended playback streams do not trigger false task cancellations. | **PASSED** | 3.65s |
| `test_phase2_producer_lifecycle.py` | Executes 50 rapid stream abort and seek cycles. Asserts `_put_chunk()` does not deadlock. | **PASSED** | 0.48s |
| `test_generator_teardown_all_states.py` | Validates resource reclamation of `finally:` blocks. | **PASSED** | 0.03s |
| `test_phase4_rust_session_and_chunks.py` | Tests authentication (401 Unauthorized, 200 OK) for Rust API. | **PASSED** | 0.44s |
| `test_session_security_rigorous.py` | Security compliance and secret leakage checks. | **PASSED** | 0.22s |
| `test_phase6_loop_rebinding.py` | Verifies event loop switch and client re-initialization. | **PASSED** | 0.67s |

---

### 2.2 Core Streaming & Transport Regression Suite

| Test File | Scope Verified | Result |
|---|---|---|
| `test_stream_engine_unified.py` | Pipelined producer, dual-anchor, seek tracking. | **PASSED** |
| `test_rust_bridge.py` | Circuit breaker trips, health check fallback. | **PASSED** |
| `test_vlc_playback_grants.py` | Security playback tokens, IP affinity. | **PASSED** |
| `test_fast_seek.py` | Dynamic seek offset alignment. | **PASSED** |
| `test_chunk_based_resume.py` | Chunk-level resume markers. | **PASSED** |
| `test_stream_buffer.py` | RAM sliding buffer, memory limits. | **PASSED** |
| `test_stream_cache.py` | Disk chunk cache tier, LRU. | **PASSED** |
| `test_range_and_media_gzip.py` | HTTP 206 parsing. | **PASSED** |
| `test_vlc_resume_purge.py` | Stale stream iterator cleanup. | **PASSED** |

**Python Suite Execution Command**:
```bash
PYTHONPATH=backend ./venv/bin/pytest \
  backend/tests/test_phase1_vlc_probe_coexistence.py \
  backend/tests/test_phase2_producer_lifecycle.py \
  backend/tests/test_generator_teardown_all_states.py \
  backend/tests/test_phase4_rust_session_and_chunks.py \
  backend/tests/test_session_security_rigorous.py \
  backend/tests/test_phase6_loop_rebinding.py \
  backend/tests/test_rust_bridge.py \
  backend/tests/test_vlc_playback_grants.py \
  backend/tests/test_fast_seek.py \
  backend/tests/test_chunk_based_resume.py \
  backend/tests/test_stream_buffer.py \
  backend/tests/test_stream_cache.py \
  backend/tests/test_range_and_media_gzip.py \
  backend/tests/test_vlc_resume_purge.py \
  backend/tests/test_stream_engine_unified.py
```
**Output**:
```
============================= 110 passed in 19.58s =============================
```

---

### 2.3 Rust MTProto Core Unit Test Suite

| Test Identifier | Verification Scope | Result |
|---|---|---|
| `test_telethon_string_session_decode` | Session string validation. | **PASSED** |
| `test_token_bucket_initialization_and_rate` | Token bucket limits. | **PASSED** |
| `test_antiban_pacer_aimd_increase` | AIMD rate ramping. | **PASSED** |
| `test_antiban_pacer_flood_cooldown` | Flood wait enforcement. | **PASSED** |
