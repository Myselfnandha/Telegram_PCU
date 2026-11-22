# PRODUCT_ARCHITECTURE_GUARDRAILS.md

## 1. Frozen Streaming Subsystems
The following components are strictly **FROZEN**. No redesign, refactoring, or modification of logic is permitted:
- Python streaming engine (`backend/app/services/stream_engine.py`).
- Rust MTProto streaming core (`rust_core/`).
- Telethon and Grammers integration.
- Chunk producer/consumer pipeline.
- Streaming caches (RAM/disk).

## 2. Protected Interfaces
- Rust `/health` and streaming API endpoints.
- Python backend API contract.

## 3. Allowed Extension Points
- Additive UI components (Frontend/ReUI).
- New API routes (if not impacting performance/frozen logic).
- Non-frozen service logic (e.g., settings management).

## 4. Regression Prevention
- All new code requires unit test coverage.
- Existing tests (110 Python + 4 Rust) must always pass.
