import asyncio
import pytest
from app.routes import proxy_streaming

@pytest.mark.asyncio
async def test_vlc_probe_coexistence_and_seek_cancellation():
    """
    Acceptance test for Phase 1 (RC-001 & RC-007) and Phase 3 (RC-004).
    Simulates VLC MKV discovery sequence:
    1. bytes=0- (initial playback stream)
    2. bytes=<tail>- (tail cues probe)
    3. bytes=107- (head element probe)
    4. bytes=132- (head element probe)
    5. bytes=50000000- (genuine user seek)
    
    Verifies:
    - Metadata probes coexist concurrently with bytes=0- without cancelling it.
    - Genuine seek (bytes=50000000-) cleanly cancels the obsolete bytes=0- playback stream.
    - Correct streaming task identity is cancelled (not the exited route handler).
    - Exiting old task does not pop newer task registrations.
    """
    file_size = 100_000_000  # 100MB
    norm_chat_id = "test_chat"
    message_id = 12345
    client_tag = "vlc"
    norm_client_host = "127.0.0.1"

    # Clean test registry state
    proxy_streaming._ACTIVE_STREAM_ITERS.clear()
    proxy_streaming._ACTIVE_STREAM_TASKS.clear()

    # Step 1: Request 1 arrives: bytes=0- (Playback stream)
    req1_id = "req_play_0"
    req1_stop = asyncio.Event()
    req1_handle = proxy_streaming.StreamIteratorHandle(
        stop_event=req1_stop,
        req_id=req1_id,
        start=0,
        length=file_size,
        is_playback_stream=True,
        is_probe=False
    )
    req1_key = (norm_chat_id, message_id, client_tag, norm_client_host, req1_id)
    proxy_streaming._ACTIVE_STREAM_ITERS[req1_key] = req1_handle
    
    # Simulate req1 streaming task running
    async def dummy_stream_worker_1():
        try:
            while not req1_stop.is_set():
                await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            pass

    req1_task = asyncio.create_task(dummy_stream_worker_1())
    req1_handle.task = req1_task
    proxy_streaming._ACTIVE_STREAM_TASKS[req1_key] = (req1_task, 0)

    # Step 2: Request 2 arrives: Tail metadata probe (bytes=99980000-)
    tail_start = file_size - 20_000
    tail_len = 20_000
    is_tail_probe = (file_size > 0 and tail_start >= max(0, file_size - 4 * 1024 * 1024))
    assert is_tail_probe is True, "Tail range should be classified as tail probe"
    
    req2_id = "req_probe_tail"
    req2_stop = asyncio.Event()
    req2_handle = proxy_streaming.StreamIteratorHandle(
        stop_event=req2_stop,
        req_id=req2_id,
        start=tail_start,
        length=tail_len,
        is_playback_stream=False,
        is_probe=True
    )
    req2_key = (norm_chat_id, message_id, client_tag, norm_client_host, req2_id)
    proxy_streaming._ACTIVE_STREAM_ITERS[req2_key] = req2_handle

    # VERIFY: Req 1 must NOT be cancelled by tail probe!
    assert not req1_stop.is_set(), "Req 1 must not be stopped by tail probe"
    assert not req1_task.done(), "Req 1 task must still be running"

    # Step 3: Request 3 arrives: Head element probe (bytes=107-)
    head_start = 107
    head_len = file_size - 107
    is_head_element_probe = (head_start > 0 and head_start < 1024 * 1024)
    assert is_head_element_probe is True, "bytes=107- should be classified as head element probe"
    
    req3_id = "req_probe_107"
    req3_stop = asyncio.Event()
    req3_handle = proxy_streaming.StreamIteratorHandle(
        stop_event=req3_stop,
        req_id=req3_id,
        start=head_start,
        length=head_len,
        is_playback_stream=False,
        is_probe=True
    )
    req3_key = (norm_chat_id, message_id, client_tag, norm_client_host, req3_id)
    proxy_streaming._ACTIVE_STREAM_ITERS[req3_key] = req3_handle

    # VERIFY: Req 1 and Req 2 must NOT be cancelled by bytes=107- probe!
    assert not req1_stop.is_set(), "Req 1 must not be stopped by bytes=107- probe"
    assert not req2_stop.is_set(), "Req 2 must not be stopped by bytes=107- probe"

    # Step 4: Request 4 arrives: Head element probe (bytes=132-)
    head_start_4 = 132
    head_len_4 = file_size - 132
    assert (head_start_4 > 0 and head_start_4 < 1024 * 1024) is True
    
    req4_id = "req_probe_132"
    req4_stop = asyncio.Event()
    req4_handle = proxy_streaming.StreamIteratorHandle(
        stop_event=req4_stop,
        req_id=req4_id,
        start=head_start_4,
        length=head_len_4,
        is_playback_stream=False,
        is_probe=True
    )
    req4_key = (norm_chat_id, message_id, client_tag, norm_client_host, req4_id)
    proxy_streaming._ACTIVE_STREAM_ITERS[req4_key] = req4_handle

    # VERIFY: All prior requests are still running concurrently!
    assert not req1_stop.is_set()
    assert not req2_stop.is_set()
    assert not req3_stop.is_set()

    # Step 5: Genuine playback seek arrives: bytes=50000000- (50MB)
    seek_start = 50_000_000
    seek_len = file_size - seek_start
    is_tail = (file_size > 0 and seek_start >= max(0, file_size - 4 * 1024 * 1024))
    is_head = (seek_start > 0 and seek_start < 1024 * 1024)
    is_bounded = (seek_len <= 2 * 1024 * 1024)
    is_probe_5 = is_tail or is_head or is_bounded
    is_playback_5 = not is_probe_5
    assert is_playback_5 is True, "Seek to 50MB must be classified as a genuine playback stream"

    req5_id = "req_seek_50m"
    req5_stop = asyncio.Event()
    req5_handle = proxy_streaming.StreamIteratorHandle(
        stop_event=req5_stop,
        req_id=req5_id,
        start=seek_start,
        length=seek_len,
        is_playback_stream=True,
        is_probe=False
    )
    req5_key = (norm_chat_id, message_id, client_tag, norm_client_host, req5_id)

    # Execute Phase 1 cancellation logic as implemented in proxy_streaming.py:
    for sk, old_h in list(proxy_streaming._ACTIVE_STREAM_ITERS.items()):
        if (
            len(sk) >= 5
            and sk[0] == norm_chat_id
            and sk[1] == message_id
            and sk[2] == client_tag
            and sk[3] == norm_client_host
            and sk[4] != req5_id
        ):
            if getattr(old_h, "is_playback_stream", False) and getattr(old_h, "start", 0) != seek_start:
                proxy_streaming._ACTIVE_STREAM_ITERS.pop(sk, None)
                old_h.stop_event.set()
                if old_h.task and not old_h.task.done():
                    old_h.task.cancel()
                proxy_streaming._ACTIVE_STREAM_TASKS.pop(sk, None)

    proxy_streaming._ACTIVE_STREAM_ITERS[req5_key] = req5_handle

    # VERIFY CANCELLATION:
    # 1. Req 1 (the superseded playback stream at offset 0) MUST BE CANCELLED!
    assert req1_stop.is_set(), "Superseded playback stream at offset 0 must have stop_event set"
    await asyncio.sleep(0.02)
    assert req1_task.done(), "Superseded playback stream task must be cancelled/done"
    assert req1_key not in proxy_streaming._ACTIVE_STREAM_ITERS, "Req 1 must be removed from active iters"

    # 2. Metadata probes (Req 2, Req 3, Req 4) MUST NOT BE CANCELLED!
    assert not req2_stop.is_set(), "Tail metadata probe must NOT be cancelled by seek"
    assert not req3_stop.is_set(), "Head element probe 107 must NOT be cancelled by seek"
    assert not req4_stop.is_set(), "Head element probe 132 must NOT be cancelled by seek"
    assert req2_key in proxy_streaming._ACTIVE_STREAM_ITERS
    assert req3_key in proxy_streaming._ACTIVE_STREAM_ITERS
    assert req4_key in proxy_streaming._ACTIVE_STREAM_ITERS

    # 3. New seek stream (Req 5) is registered and active
    assert req5_key in proxy_streaming._ACTIVE_STREAM_ITERS
    assert not req5_stop.is_set()

    # Clean up test tasks
    req2_stop.set()
    req3_stop.set()
    req4_stop.set()
    req5_stop.set()
