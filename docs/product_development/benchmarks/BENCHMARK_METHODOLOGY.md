# BENCHMARK_METHODOLOGY.md

## 1. Objective
Establish a reproducible baseline for library loading performance across varying dataset sizes to identify performance bottlenecks in frontend rendering and backend API serialization.

## 2. Methodology
- **Environments**: Chrome 128+ (latest stable).
- **Dataset Sizes**: 100, 1,000, 5,000, 10,000 entries.
- **Trial Count**: 5 trials per size.
- **Measurements**:
  - **Backend**: API response latency, JSON serialization time, DB query time.
  - **Frontend**: `JSON.parse` duration, TBT (Total Blocking Time), DOM node count, First usable card visibility, LCP.

## 3. Execution Plan
1. Generate synthetic media metadata files for each dataset size (100, 1k, 5k, 10k).
2. Backend: Benchmark API response and serialization using `wrk` or similar tool.
3. Frontend: Use Chrome Performance Tab to measure rendering and script execution metrics.
4. Report: Consolidate metrics into `FRONTEND_BENCHMARK_RESULTS.md` and `BACKEND_BENCHMARK_RESULTS.md`.

## 4. Guardrails
- **Frozen Subsystems**: No changes to `backend/app/services/stream_engine.py`, `rust_core/`, etc.
- **Non-Invasive**: All tests use synthetic/mocked endpoints where applicable to prevent production data corruption.
