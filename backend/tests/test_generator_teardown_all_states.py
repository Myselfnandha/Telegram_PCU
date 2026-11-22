import asyncio
import inspect
import pytest

@pytest.mark.asyncio
async def test_generator_cleanup_suspended_state():
    inner_cleaned_up = False

    async def inner_stream():
        nonlocal inner_cleaned_up
        try:
            for i in range(100):
                yield b"chunk_" + str(i).encode()
        finally:
            inner_cleaned_up = True

    gen = inner_stream()
    # Pull 1 chunk to transition generator from AGEN_CREATED to AGEN_SUSPENDED
    first = await gen.asend(None)
    assert first == b"chunk_0"
    state = inspect.getasyncgenstate(gen)
    assert state == inspect.AGEN_SUSPENDED

    # Simulate teardown: closing a suspended generator MUST execute finally block immediately
    if state in (inspect.AGEN_CREATED, inspect.AGEN_SUSPENDED):
        await gen.aclose()

    assert inner_cleaned_up is True
    assert inspect.getasyncgenstate(gen) == inspect.AGEN_CLOSED

@pytest.mark.asyncio
async def test_generator_cleanup_created_state():
    inner_cleaned_up = False

    async def inner_stream():
        nonlocal inner_cleaned_up
        try:
            for i in range(100):
                yield b"chunk"
        finally:
            inner_cleaned_up = True

    gen = inner_stream()
    state = inspect.getasyncgenstate(gen)
    assert state == inspect.AGEN_CREATED

    # An un-entered generator transitions directly to AGEN_CLOSED upon aclose()
    if state in (inspect.AGEN_CREATED, inspect.AGEN_SUSPENDED):
        await gen.aclose()

    assert inspect.getasyncgenstate(gen) == inspect.AGEN_CLOSED
