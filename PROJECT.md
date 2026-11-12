# Project: Unified Zero-Config High-Performance MTProto Streaming Architecture

## Architecture
Autonomous, zero-config high-speed MTProto streaming engine for TG_TOOL that unifies all 6 performance strategies:
1. **Tier 1 & Tier 2 Cache Layer**: In-memory RAM ring buffer (256 MB) in `stream_buffer.py` + persistent NVMe/disk block cache at `/var/tmp/tg_cache` (10 GB cap, 3 GB free disk floor) in `stream_cache.py`.
2. **Client Management & Speed Governor**: Autonomous Telegram Premium tier detection in `telegram_client.py` scaling `rate_governor.py` token bucket ceilings (32 MB/s Premium, 4.5 MB/s Standard single, 9.0 MB/s Standard dual).
3. **Pre-warmed DC Sockets**: Proactive socket warming (`prewarm_dc`) keeping 2 hot MTProto sockets on active media DC with a 20-second keep-alive ping loop in `telegram_client.py`.
4. **Streaming Core Engine**: Dual-anchor prefetch (first 2 MB + last 4 MB), adaptive sliding-window lookahead (burst to 8 chunks when runway < 6, float at 2 chunks when runway >= 16), and dynamic work-stealing dispatcher routing urgent chunks to the fastest idle client account.

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| F1 | Persistent Disk Cache at `/var/tmp/tg_cache` | Block & slice storage capped at 10 GB with safe env overrides for test isolation | M1 | R3 |
| F2 | Zero-Maintenance Boot Cleanup (`startup_clean`) | Automatic purge of lingering `.tmp` and `.merging` files on service boot in `main.py` | M1 | R3 |
| F3 | Emergency Disk Guard (`check_disk_guard`) | Automatic LRU eviction if drive free space < 3 GB or cache > 10 GB using `shutil.disk_usage` | M1 | R3 |
| F4 | Cache Slice API (`get_slice`, `put_slice`) | Atomic write and discrete block/slice read for 2-tier cache integration | M1 | R3 |
| F5 | Dedicated Cache & Buffer Test Suites | Unit and integration tests in `test_stream_cache.py` and `test_stream_buffer.py` | M1 | AC |
| F6 | Telegram Premium Tier Auto-Detection | Centralized `sync_tier_limits` capturing `is_premium` across all login flows & accounts | M2 | R5 |
| F7 | Dynamic Speed Governor Ceilings | Scale ceiling to 32 MB/s for Premium, 4.5 MB/s for Standard single, 9.0 MB/s for dual Standard | M2 | R5 |
| F8 | Pre-warmed DC Socket Pool (`prewarm_dc`) | Keep 2 hot MTProto sockets on active media DC with idempotent concurrency guard | M2 | R6 |
| F9 | Fixed 20s Keepalive Ping Loop | Strict 20s ping interval without corrupting idle semaphore slot counts | M2 | R6 |
| F10 | Head & Tail Dual-Anchor Prefetch | Non-blocking async background fetch of first 2 MB and last 4 MB on stream start | M3 | R1 |
| F11 | Adaptive Sliding-Window Concurrency | Burst up to 8 in-flight chunks when runway < 6, dial down to 2 paced chunks when runway >= 16 | M3 | R2 |
| F12 | 2-Tier Cache Interception in Core Engine | Query RAM ring buffer -> Disk block cache (0ms wire) -> MTProto; persist downloaded chunks to RAM & Disk | M3 | R3 |
| F13 | Dynamic Work-Stealing Dispatcher | Route urgent chunks to fastest idle account; allow idle accounts to steal lookahead chunks | M3 | R4 |
| F14 | Account Isolation & Cooling Protection | Exclude cooling accounts and isolate secondary accounts on shared hub channel permission errors | M3 | R4 |
| F15 | Comprehensive Test Verification & Hardening | Run all test suites (cache, buffer, client, fast seek, multi-account, e2e stream) with 0 regressions | M4 | AC |
| F16 | Forensic Integrity Audit | Binary veto forensic audit verifying genuine implementation, zero cheating, zero dead code | M4 | Audit |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | 2-Tier Hybrid Cache & Disk Guards | `stream_cache.py`, `main.py`, `test_stream_cache.py`, `test_stream_buffer.py` | none | DONE |
| M2 | Autonomous Tier Auto-Detection & DC Socket Pool | `telegram_client.py`, `rate_governor.py`, `test_client_tier_and_sockets.py` | none | DONE |
| M3 | Streaming Core: Dual-Anchor, Lookahead & Work-Stealing | `stream_engine.py`, core engine unit & integration tests | M1, M2 | IN_PROGRESS |
| M4 | Comprehensive Test Verification & Hardening | Full project test pass, zero regressions, forensic audit | M1, M2, M3 | PLANNED |

## Interface Contracts
### `stream_cache.py` ↔ `stream_engine.py`
- `stream_cache_service.get_slice(chat_id: str, message_id: int, offset: int, length: int) -> Optional[bytes]`
  - Returns `bytes` if cached on disk at `/var/tmp/tg_cache`, `None` on cache miss.
- `stream_cache_service.put_slice(chat_id: str, message_id: int, offset: int, data: bytes) -> bool`
  - Atomically persists `data` to disk via `.tmp.{uuid}` and `os.replace`.
- `stream_cache_service.startup_clean() -> int`
  - Purges all `.tmp*` artifacts on boot; called from `main.py` lifespan.
- `stream_cache_service.check_disk_guard() -> bool`
  - Enforces 10 GB max cache and 3 GB free disk floor; triggers LRU eviction.

### `telegram_client.py` ↔ `rate_governor.py` & `stream_engine.py`
- `TelegramClientManager.sync_tier_limits() -> None`
  - Evaluates primary and secondary accounts and updates `rate_governor.configure_for_tier`.
- `rate_governor.configure_for_tier(is_premium: bool, secondary_premium: Optional[bool] = None)`
  - Single Premium: 32.0 MB/s rate ceiling, 16.0 MB/s min, 36.0 MB/s max, 16.0 MB capacity.
  - Standard single: 4.5 MB/s rate ceiling.
  - Dual Standard: 9.0 MB/s combined ceiling.
- `sender_pool.prewarm_dc(client: Any, dc_id: int, target_hot_senders: int = 2) -> None`
  - Keeps 2 hot sockets ready per active DC.

## Code Layout
- `backend/app/services/stream_cache.py`: Tier 2 disk block cache, cleanup, disk guard
- `backend/app/services/stream_buffer.py`: Tier 1 RAM ring buffer (256 MB)
- `backend/app/services/rate_governor.py`: Token bucket rate governor
- `backend/app/services/stream_engine.py`: Core streaming engine, pipelined producer, work-stealing dispatcher
- `backend/app/telegram_client.py`: Telegram client manager, sender pool, keepalive loop
- `backend/app/main.py`: Application lifespan and periodic maintenance hooks
- `backend/tests/test_stream_cache.py`: Cache unit & integration tests
- `backend/tests/test_stream_buffer.py`: Buffer unit & integration tests
- `backend/tests/test_client_tier_and_sockets.py`: Client tier & DC socket tests
- `backend/tests/test_challenger_2_m1_it2.py`: Channel ID normalization & RAM ceiling challenge tests
- `backend/tests/test_challenger_m1_stress.py`: Concurrency and disk sweep stress tests
- `backend/tests/test_challenger_m2_boundaries.py`: Account lifecycle boundary tests
- `backend/tests/test_challenger_m2_empirical.py`: Prewarm & rate governor stress tests
