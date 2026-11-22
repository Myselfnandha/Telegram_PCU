# FORENSIC EVIDENCE: RC-004 ASYNC GENERATOR ACLOSE() COLLISION

**Target Subsystem**: `backend/app/routes/proxy_streaming.py:683-688`  
**Evidence Source**: `backend/data/tg_power_suite.log`  
**Total Incidents**: 16 occurrences on `2026-11-17`.

---

## 1. Traceback Evidence from Logs

Excerpt from `backend/data/tg_power_suite.log` at `17:28:56,254` during `The Threesome (2025)`:

```log
2026-11-17 17:28:56,254 [ERROR] [asyncio] Task exception was never retrieved
future: <Task finished name='Task-3703' coro=<<async_generator_athrow without __name__>()> exception=RuntimeError('aclose(): asynchronous generator is already running')>
RuntimeError: aclose(): asynchronous generator is already running
2026-11-17 17:28:56,321 [INFO] [stream_engine] STREAM [The Threesome (2025) DL ShowBoxMovieChannel.mkv] Chunk at 316,669,952: 1,048,576B in 2.77s (0.4 MB/s) from DC 4
2026-11-17 17:28:56,389 [INFO] [stream_engine] STREAM [The Threesome (2025) DL ShowBoxMovieChannel.mkv] Chunk at 317,718,528: 1,048,576B in 2.83s (0.4 MB/s) from DC 4
```

Second occurrence at `17:29:12,224`:

```log
2026-11-17 17:29:12,224 [ERROR] [asyncio] Task exception was never retrieved
future: <Task finished name='Task-3875' coro=<<async_generator_athrow without __name__>()> exception=RuntimeError('aclose(): asynchronous generator is already running')>
RuntimeError: aclose(): asynchronous generator is already running
2026-11-17 17:29:12,333 [INFO] [stream_engine] STREAM [The Threesome (2025) DL ShowBoxMovieChannel.mkv] Chunk at 349,175,808: 1,048,576B in 1.50s (0.7 MB/s) from DC 4
```

Third occurrence at `17:43:00,992` during `Fatal Seduction S01E07`:

```log
2026-11-17 17:43:00,992 [ERROR] [asyncio] Task exception was never retrieved
future: <Task finished name='Task-6689' coro=<<async_generator_athrow without __name__>()> exception=RuntimeError('aclose(): asynchronous generator is already running')>
RuntimeError: aclose(): asynchronous generator is already running
2026-11-17 17:43:01,151 [INFO] [stream_engine] STREAM [Fatal Seduction S01E07 NF GalaxyTV.mkv] Chunk at 30,408,704: 1,048,576B in 1.90s (0.5 MB/s) from DC 4
```

---

## 2. Trigger Mechanics in Source Code

In `backend/app/routes/proxy_streaming.py:673-688`:

```python
try:
    async for chunk in gen:
        if stop_event.is_set():
            return
        yield chunk
except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError, ClientDisconnect):
    return
except Exception as e:
    logger.debug(f"Stream wrapper notice for {clean_name}: {e}")
    return
finally:
    if hasattr(gen, "aclose"):
        try:
            await gen.aclose()
        except (RuntimeError, Exception, asyncio.CancelledError):
            pass
```

When a seek request arrives, `old_t.cancel()` cancels the task executing `stream_wrapper()`.
* If `stream_wrapper` is currently executing `async for chunk in gen:`, the cancellation enters the generator via `gen.athrow(CancelledError)`.
* Before `athrow` finishes executing the inner `finally:` block of `stream_media`, `stream_wrapper`'s outer `finally:` block executes `await gen.aclose()`.
* Python PEP 525 mandates that calling `aclose()` on an already running/unwinding generator is illegal and raises `RuntimeError('aclose(): asynchronous generator is already running')`.
* Because `gen.aclose()` was called from an exception unwinding context, Python marks the task exception as unretrieved and prints it to stderr.
