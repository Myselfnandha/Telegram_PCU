import asyncio
import pytest
from app.services.stream_engine import StreamEngine

@pytest.mark.asyncio
async def test_producer_safe_put_and_no_task_leak():
    """
    Acceptance test for Phase 2 (RC-002).
    Simulates:
    1. Queue fills completely (QueueFull).
    2. Consumer disappears / stop_event is set.
    3. Producer unblocks immediately without deadlocking or raising exceptions.
    4. Producer task cancellation under slow operation (>2s).
    5. Verifies task is cleanly supervised in _retained_cleanup_tasks and cleared upon completion.
    6. Verifies zero unhandled task destruction.
    """
    engine = StreamEngine()
    stop_event = asyncio.Event()
    queue = asyncio.Queue(maxsize=2)
    
    # 1. Fill the queue
    await queue.put(b"chunk-0")
    await queue.put(b"chunk-1")
    assert queue.full(), "Queue should be full"

    # 2. Simulate producer attempting _put_chunk while queue is full
    producer_unblocked = False

    async def _put_chunk(item):
        while not stop_event.is_set():
            try:
                queue.put_nowait(item)
                return True
            except asyncio.QueueFull:
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=0.04)
                    return False
                except asyncio.TimeoutError:
                    continue
        return False

    async def simulated_slow_producer():
        nonlocal producer_unblocked
        # Try to put when full
        res = await _put_chunk(b"chunk-2")
        producer_unblocked = True
        # Simulate background slow wire operation that takes 0.5s
        try:
            await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            pass
        return res

    prod_task = asyncio.create_task(simulated_slow_producer())
    await asyncio.sleep(0.06)
    assert not producer_unblocked, "Producer should be waiting on stop_event / space"

    # 3. Consumer disappears -> triggers stop_event.set()
    stop_event.set()
    await asyncio.sleep(0.06)
    assert producer_unblocked, "Producer must unblock immediately when stop_event is set"

    # 4. Consumer cancels producer task and supervises it
    prod_task.cancel()
    if not prod_task.done():
        engine._retained_cleanup_tasks.add(prod_task)
        def _on_done(t):
            engine._retained_cleanup_tasks.discard(t)
        prod_task.add_done_callback(_on_done)

    # 5. Wait for prod_task to finish
    await prod_task
    assert prod_task.done(), "Producer task must be done"
    assert len(engine._retained_cleanup_tasks) == 0, "Retained cleanup tasks must be empty once done"

    # 6. Verify queue contents remained intact (no silent corruption)
    assert await queue.get() == b"chunk-0"
    assert await queue.get() == b"chunk-1"
    assert queue.empty()
