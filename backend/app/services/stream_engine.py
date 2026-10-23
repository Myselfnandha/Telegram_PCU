import asyncio
import time
import logging
from typing import Optional, Union, AsyncGenerator, Any
from telethon import utils, errors
from telethon.tl.functions.upload import GetFileRequest

from app.telegram_client import sender_pool
from app.services.rate_governor import rate_governor
from app.services.stream_buffer import stream_buffer

logger = logging.getLogger("stream_engine")

FILE_REF_ERRORS = (
    getattr(errors, "FileReferenceExpiredError", Exception),
    getattr(errors, "FileReferenceInvalidError", Exception),
    getattr(errors, "MediaEmptyError", Exception)
)


class StreamEngine:
    """
    Deep Module for High-Speed MTProto Streaming.
    Hides all chunk-alignment arithmetic, concurrent lookahead pipelining,
    multi-socket MTProto sender borrowing, rate governor permit budgeting,
    RAM prefix handoffs, and error resilience behind a single async generator interface.
    """

    async def stream_media(
        self,
        client: Any,
        chat_id: Union[int, str],
        message_id: int,
        start: int,
        length: int,
        file_size: int = 0,
        is_media_player: bool = False,
        clean_name: str = "media",
        cached_prefix: Optional[bytes] = None,
        message: Optional[Any] = None,
        get_fresh_message_fn: Optional[Any] = None,
        external_stop_event: Optional[asyncio.Event] = None
    ) -> AsyncGenerator[bytes, None]:
        """
        Stream arbitrary byte ranges directly from Telegram MTProto with zero-latency
        RAM prefix delivery and lookahead pipelining.
        """
        t0 = time.time()
        bytes_sent = 0
        first_chunk_sent = False
        effective_start = start

        # 1. Deliver pre-cached RAM prefix immediately (0.000s instant start)
        if cached_prefix:
            first_chunk_sent = True
            logger.info(f"STREAM [{clean_name}] RAM prefix hit: {len(cached_prefix):,}B delivered in {time.time() - t0:.3f}s (0ms TTFB)")
            yield cached_prefix
            bytes_sent += len(cached_prefix)
            effective_start += len(cached_prefix)
            if bytes_sent >= length:
                logger.info(f"STREAM [{clean_name}] Complete (RAM prefix only): {bytes_sent:,}B in {time.time() - t0:.3f}s")
                return

        # 2. Compute optimal chunk size: 128KB for small probes/tails, 1MB for media streaming (Telegram standard)
        if length <= 256 * 1024 or (file_size > 0 and effective_start >= (file_size - 4 * 1024 * 1024)):
            chunk_size = 128 * 1024
        else:
            chunk_size = 1024 * 1024

        remaining_length = length - bytes_sent
        aligned_offset = (effective_start // chunk_size) * chunk_size
        discard_front = effective_start - aligned_offset
        total_chunks = (remaining_length + discard_front + chunk_size - 1) // chunk_size

        # Resolve media location and DC ID
        file_info = utils._get_file_info(message.media) if (message and hasattr(message, "media")) else None
        file_loc = getattr(file_info, "location", None)
        dc_id = getattr(file_info, "dc_id", None) or getattr(getattr(client, "session", None), "dc_id", 5)

        session_dc = getattr(getattr(client, "session", None), "dc_id", None)
        is_foreign = bool(dc_id and session_dc and dc_id != session_dc)

        logger.info(
            f"STREAM [{clean_name}] Starting range: offset={effective_start:,}, "
            f"length={remaining_length:,}B ({remaining_length / (1024*1024):.2f}MB), "
            f"chunk_size={chunk_size // 1024}KB, total_chunks={total_chunks}, DC={dc_id}"
        )

        queue: asyncio.Queue = asyncio.Queue(maxsize=128)
        stop_event = external_stop_event or asyncio.Event()

        async def fetch_one_chunk(c_idx: int):
            nonlocal file_loc, message
            if stop_event.is_set():
                raise asyncio.CancelledError()
            c_offset = aligned_offset + (c_idx * chunk_size)

            # Check RAM sliding window first (0ms instant hit on backward/forward scrub)
            cached_data = stream_buffer.get_slice(str(chat_id), message_id, c_offset, chunk_size)
            if cached_data and len(cached_data) == chunk_size:
                logger.info(f"STREAM [{clean_name}] Chunk {c_idx+1}/{total_chunks} at {c_offset:,}: RAM buffer hit (0ms)")
                return c_idx, cached_data

            req = GetFileRequest(file_loc, offset=c_offset, limit=chunk_size)

            for attempt in range(6):
                if stop_event.is_set():
                    raise asyncio.CancelledError()
                try:
                    c_t0 = time.time()
                    await rate_governor.acquire_chunk_slot(chunk_size)
                    chunk_timeout = 6.0 if is_media_player else 15.0
                    async with sender_pool.borrow(client, dc_id) as s:
                        res = await asyncio.wait_for(
                            client._call(s, req, flood_sleep_threshold=0),
                            timeout=chunk_timeout
                        )
                    c_dt = max(0.001, time.time() - c_t0)
                    if hasattr(res, "bytes"):
                        data = res.bytes
                    elif isinstance(res, (bytes, bytearray)):
                        data = bytes(res)
                    else:
                        data = b""

                    # Push directly to stream_buffer NOW so short forward/backward seeks are instant RAM hits
                    stream_buffer.push(str(chat_id), message_id, c_offset, data)
                    if file_size > 0 and c_offset >= (file_size - 4 * 1024 * 1024):
                        stream_buffer.push_tail(str(chat_id), message_id, c_offset, data)

                    c_speed = (len(data) / (1024 * 1024)) / c_dt
                    logger.info(
                        f"STREAM [{clean_name}] Chunk {c_idx+1}/{total_chunks} at {c_offset:,}: "
                        f"{len(data):,}B in {c_dt:.2f}s ({c_speed:.1f} MB/s) from DC {dc_id}"
                    )
                    return c_idx, data

                except (asyncio.TimeoutError, ConnectionError, BrokenPipeError, OSError) as conn_err:
                    err_str = f"{type(conn_err).__name__}: {conn_err}" if str(conn_err) else type(conn_err).__name__
                    logger.warning(f"STREAM [{clean_name}] Network/socket glitch on DC {dc_id} (attempt {attempt+1}/6): {err_str}")

                    # Auto-heal: verify and restore client connection across network switches (WiFi -> Hotspot/Cellular)
                    try:
                        from app.telegram_client import TelegramClientManager
                        await TelegramClientManager.ensure_connected(timeout=6.0)
                    except Exception as rec_err:
                        logger.debug(f"Reconnection attempt: {rec_err}")

                    backoff = min(3.0, 0.3 * (1.8 ** attempt))
                    await asyncio.sleep(backoff)
                    continue

                except errors.FloodWaitError as fwe:
                    wait_time = max(1, int(fwe.seconds))
                    rate_governor.report_flood_wait(wait_time, f"FloodWait DC {dc_id}", client=client)
                    logger.warning(f"STREAM [{clean_name}] FloodWait {wait_time}s on DC {dc_id}, pausing workers for safe cooldown...")
                    await asyncio.sleep(wait_time + 0.5)
                    continue

                except FILE_REF_ERRORS:
                    logger.info(f"STREAM [{clean_name}] File ref expired on chunk {c_idx+1}/{total_chunks}, refreshing...")
                    if get_fresh_message_fn:
                        refreshed = await get_fresh_message_fn(client, chat_id, message_id, force_refresh=True)
                        if refreshed and hasattr(refreshed, "media"):
                            message = refreshed
                            ref_info = utils._get_file_info(refreshed.media)
                            if ref_info and hasattr(ref_info, "location"):
                                file_loc = ref_info.location
                                req = GetFileRequest(file_loc, offset=c_offset, limit=chunk_size)
                    await asyncio.sleep(0.3)
                    continue

                except asyncio.CancelledError:
                    raise
                except Exception as fe:
                    err_str = f"{type(fe).__name__}: {fe}" if str(fe) else type(fe).__name__
                    if attempt >= 5:
                        logger.warning(f"STREAM [{clean_name}] Chunk {c_idx+1}/{total_chunks} failed (attempt {attempt+1}): {err_str}")
                        raise
                    else:
                        logger.debug(f"STREAM [{clean_name}] Chunk {c_idx+1}/{total_chunks} retry {attempt+1}: {err_str}")
                    await asyncio.sleep(0.4)

        start_chunk_idx = 0
        last_progress_log = time.time()
        delivered_chunks = 0

        # Fast Seek / Instant Start: Fetch and deliver seek target chunk 0 directly for sub-second TTFB
        if is_media_player and total_chunks > 0 and not first_chunk_sent and bytes_sent < length:
            try:
                c_idx, data = await fetch_one_chunk(0)
                first_chunk_sent = True
                ttfb = time.time() - t0
                logger.info(f"STREAM [{clean_name}] Seek target chunk 1/{total_chunks} ({len(data):,}B) delivered in {ttfb:.3f}s (TTFB)")
                chunk_slice = data
                if discard_front > 0:
                    chunk_slice = chunk_slice[discard_front:]
                    discard_front = 0
                remaining = length - bytes_sent
                if len(chunk_slice) > remaining:
                    chunk_slice = chunk_slice[:remaining]
                yield chunk_slice
                bytes_sent += len(chunk_slice)
                delivered_chunks += 1
                start_chunk_idx = 1
            except Exception as first_err:
                logger.debug(f"Direct chunk 0 fetch notice: {first_err}")

        producer_task = None
        if bytes_sent < length and start_chunk_idx < total_chunks:
            async def pipelined_chunk_producer():
                is_premium_rate = rate_governor.current_rate_limit_mbs > 15.0
                if is_media_player:
                    # Media Player Smooth Cruise Control:
                    # 14-24MB lookahead runway is optimal (~30-60s of video).
                    # Avoids wire burst flooding while keeping steady, stutter-free playback.
                    max_in_flight = 4 if is_premium_rate else 2
                    max_lookahead = 48 if is_premium_rate else 28
                else:
                    max_in_flight = 6 if is_premium_rate else 3
                    max_lookahead = 96 if is_premium_rate else 48

                in_flight = {}
                next_to_fetch = start_chunk_idx
                next_to_deliver = start_chunk_idx
                buffered = {}

                try:
                    while next_to_deliver < total_chunks and not stop_event.is_set():
                        # 1. Drain all sequential ready chunks from buffered into queue
                        while next_to_deliver in buffered and not stop_event.is_set():
                            data = buffered.pop(next_to_deliver)
                            await queue.put(data)
                            next_to_deliver += 1

                        if next_to_deliver >= total_chunks:
                            break

                        # 2. Adaptive Buffer-Aware Concurrency:
                        # If runway is deep (>= 10 chunks in RAM, ~5MB / 25-50s of video), cruise with 1 worker.
                        # If recovering from a seek or buffer is thin (< 4 chunks), allow up to 3 workers for standard
                        # to quickly re-establish the safety cushion.
                        runway = next_to_fetch - next_to_deliver
                        if is_media_player:
                            if runway >= 10:
                                allowed_in_flight = 1
                            elif runway >= 5:
                                allowed_in_flight = min(2, max_in_flight)
                            else:
                                allowed_in_flight = 3 if not is_premium_rate else 4
                        else:
                            allowed_in_flight = max_in_flight

                        while (
                            len(in_flight) < allowed_in_flight
                            and (next_to_fetch - next_to_deliver) < max_lookahead
                            and next_to_fetch < total_chunks
                            and not stop_event.is_set()
                        ):
                            t = asyncio.create_task(fetch_one_chunk(next_to_fetch))
                            in_flight[t] = next_to_fetch
                            next_to_fetch += 1

                        # 3. If tasks are downloading, wait for at least one to finish
                        if in_flight:
                            done, _ = await asyncio.wait(list(in_flight.keys()), return_when=asyncio.FIRST_COMPLETED)
                            for d in done:
                                c_idx = in_flight.pop(d, None)
                                try:
                                    _, data = d.result()
                                    buffered[c_idx] = data
                                except asyncio.CancelledError:
                                    stop_event.set()
                                    break
                                except Exception as e:
                                    logger.warning(f"STREAM [{clean_name}] Chunk {c_idx} failed: {type(e).__name__} ({e or 'timeout'}). Retrying...")
                                    if not stop_event.is_set():
                                        t = asyncio.create_task(fetch_one_chunk(c_idx))
                                        in_flight[t] = c_idx
                        else:
                            # 4. If no tasks in flight:
                            if next_to_fetch >= total_chunks:
                                # All chunks fetched from wire; wait until remaining buffered chunks drain
                                if next_to_deliver not in buffered:
                                    break
                            else:
                                # Lookahead window is completely full (96 chunks in RAM) and waiting for player to consume from queue
                                if next_to_deliver in buffered:
                                    continue
                                await asyncio.sleep(0.05)

                    await queue.put(None)
                except asyncio.CancelledError:
                    logger.debug(f"STREAM [{clean_name}] Pipelined producer cancelled")
                except Exception as e:
                    logger.debug(f"STREAM [{clean_name}] Producer notice: {e}")
                finally:
                    # Graceful Drain: If stop_event was set, allow active in-flight chunk downloads
                    # up to 350ms to finish receiving their bytes off the MTProto wire.
                    # Finished chunks go into stream_buffer and return clean, warm senders to the pool.
                    if in_flight:
                        done, pending = await asyncio.wait(list(in_flight.keys()), timeout=0.35)
                        for d in done:
                            try:
                                d.result()
                            except Exception:
                                pass
                        for p in pending:
                            p.cancel()
                        if pending:
                            await asyncio.gather(*pending, return_exceptions=True)
                    try:
                        queue.put_nowait(None)
                    except Exception:
                        pass

            producer_task = asyncio.create_task(pipelined_chunk_producer())

        try:
            feed_pos = aligned_offset + (start_chunk_idx * chunk_size)
            while bytes_sent < length:
                chunk = await queue.get()
                if chunk is None:
                    if bytes_sent < length:
                        logger.debug(f"STREAM [{clean_name}] Pipeline stopped early: sent {bytes_sent:,}/{length:,} bytes")
                        raise ConnectionResetError(f"Stream aborted before full length: sent {bytes_sent}/{length} bytes")
                    break

                if not first_chunk_sent:
                    first_chunk_sent = True
                    ttfb = time.time() - t0
                    logger.info(f"STREAM [{clean_name}] First chunk ({len(chunk):,}B) delivered in {ttfb:.2f}s (TTFB)")

                feed_pos += len(chunk)

                if discard_front > 0:
                    if len(chunk) <= discard_front:
                        discard_front -= len(chunk)
                        continue
                    chunk = chunk[discard_front:]
                    discard_front = 0

                remaining = length - bytes_sent
                if len(chunk) > remaining:
                    chunk = chunk[:remaining]

                yield chunk
                bytes_sent += len(chunk)
                delivered_chunks += 1

                # Periodic throughput progress logging every 5 seconds
                now = time.time()
                if (now - last_progress_log) >= 5.0 and bytes_sent > 0:
                    last_progress_log = now
                    elapsed = max(0.01, now - t0)
                    playback_speed = (bytes_sent / (1024 * 1024)) / elapsed
                    pct = (bytes_sent * 100.0 / length) if length > 0 else 0
                    q_len = queue.qsize()
                    buffered_mb = (q_len * chunk_size) / (1024 * 1024)
                    logger.info(
                        f"STREAM [{clean_name}] Progress: {bytes_sent:,}/{length:,}B "
                        f"({pct:.1f}%) | Player Rate: {playback_speed:.2f} MB/s | Buffer Runway: ~{buffered_mb:.1f}MB ({q_len} chunks in RAM) | {delivered_chunks} delivered"
                    )

            elapsed = max(0.01, time.time() - t0)
            avg_speed = (bytes_sent / (1024 * 1024)) / elapsed
            logger.info(
                f"STREAM [{clean_name}] Complete: sent {bytes_sent:,}/{length:,}B "
                f"in {elapsed:.2f}s ({avg_speed:.2f} MB/s)"
            )

        except (ConnectionResetError, BrokenPipeError) as ce:
            elapsed = max(0.01, time.time() - t0)
            logger.info(
                f"STREAM [{clean_name}] Client seek/disconnect at byte {start + bytes_sent:,} "
                f"(delivered {bytes_sent:,}/{length:,}B in {elapsed:.2f}s)"
            )
            raise
        except asyncio.CancelledError:
            elapsed = max(0.01, time.time() - t0)
            logger.info(
                f"STREAM [{clean_name}] Stream cancelled at byte {start + bytes_sent:,} "
                f"(delivered {bytes_sent:,}/{length:,}B in {elapsed:.2f}s)"
            )
            raise
        finally:
            stop_event.set()
            if producer_task and not producer_task.done():
                try:
                    # Allow producer up to 400ms to let in-flight chunks finish and return warm senders to the pool
                    await asyncio.wait_for(asyncio.shield(producer_task), timeout=0.40)
                except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                    if not producer_task.done():
                        producer_task.cancel()
                        try:
                            await producer_task
                        except (asyncio.CancelledError, Exception):
                            pass


stream_engine = StreamEngine()
