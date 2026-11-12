import os
import sys
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from telethon import errors
from app.services.stream_cache import StreamCacheManager
from app.telegram_client import TelegramClientManager


@pytest.mark.asyncio
async def test_ensure_streaming_relay_channel_auto_creates(monkeypatch):
    TelegramClientManager._relay_channel_id = None
    monkeypatch.setattr("app.config.STREAMING_RELAY_CHANNEL_ID", 0)

    mock_channel = MagicMock()
    mock_channel.id = 123456789
    mock_channel.title = "TG Stream Relay"

    mock_res = MagicMock()
    mock_res.chats = [mock_channel]

    mock_primary = AsyncMock(return_value=mock_res)
    mock_sec = AsyncMock(return_value=None)

    monkeypatch.setattr(TelegramClientManager, "get_client", AsyncMock(return_value=mock_primary))
    monkeypatch.setattr(TelegramClientManager, "is_authorized", AsyncMock(return_value=True))
    monkeypatch.setattr(TelegramClientManager, "is_secondary_authorized", AsyncMock(return_value=True))
    monkeypatch.setattr(TelegramClientManager, "get_secondary_client", AsyncMock(return_value=mock_sec))
    monkeypatch.setattr("telethon.utils.get_peer_id", lambda c: -100123456789)

    saved_env = {}
    monkeypatch.setattr("app.config.update_relay_channel_id_in_env", lambda cid: saved_env.update({"cid": cid}))

    relay_id = await TelegramClientManager.ensure_streaming_relay_channel()
    assert relay_id == -100123456789
    assert TelegramClientManager.get_relay_channel_id() == -100123456789
    assert saved_env.get("cid") == -100123456789


@pytest.mark.asyncio
async def test_forward_to_relay_channel_success(monkeypatch):
    TelegramClientManager._relay_channel_id = -100123456789
    TelegramClientManager._relay_message_map.clear()

    mock_primary = MagicMock()
    mock_entity = MagicMock()
    mock_primary.get_entity = AsyncMock(return_value=mock_entity)

    mock_forwarded_msg = MagicMock()
    mock_forwarded_msg.id = 999
    mock_primary.forward_messages = AsyncMock(return_value=[mock_forwarded_msg])

    monkeypatch.setattr(TelegramClientManager, "get_client", AsyncMock(return_value=mock_primary))
    monkeypatch.setattr(TelegramClientManager, "is_authorized", AsyncMock(return_value=True))

    new_id = await TelegramClientManager.forward_to_relay_channel("original_chat", 42)
    assert new_id == 999
    assert TelegramClientManager._relay_message_map[("original_chat", 42)] == 999

    cached_id = await TelegramClientManager.forward_to_relay_channel("original_chat", 42)
    assert cached_id == 999
    assert mock_primary.forward_messages.await_count == 1


@pytest.mark.asyncio
async def test_forward_to_relay_channel_copy_protected_fallback(monkeypatch):
    TelegramClientManager._relay_channel_id = -100123456789
    TelegramClientManager._relay_message_map.clear()

    mock_primary = MagicMock()
    mock_entity = MagicMock()
    mock_primary.get_entity = AsyncMock(return_value=mock_entity)
    mock_primary.forward_messages = AsyncMock(side_effect=errors.ChatForwardsRestrictedError(request=None))

    monkeypatch.setattr(TelegramClientManager, "get_client", AsyncMock(return_value=mock_primary))
    monkeypatch.setattr(TelegramClientManager, "is_authorized", AsyncMock(return_value=True))

    new_id = await TelegramClientManager.forward_to_relay_channel("protected_chat", 55)
    assert new_id is None
    assert ("protected_chat", 55) not in TelegramClientManager._relay_message_map


def test_startup_purge_wipe_all(tmp_path, monkeypatch):
    cache_dir = tmp_path / "tg_cache"
    blocks_dir = cache_dir / "blocks"
    cache_dir.mkdir(parents=True, exist_ok=True)
    blocks_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr("app.services.stream_cache.CACHE_DIR", cache_dir)
    monkeypatch.setattr("app.services.stream_cache.BLOCKS_DIR", blocks_dir)

    b1 = blocks_dir / "chat1_1_part00000.bin"
    b1.write_bytes(b"block1")
    s1 = blocks_dir / "chat1_1_slice_0_1024.bin"
    s1.write_bytes(b"slice1")
    m1 = cache_dir / "chat1_1.mp4"
    m1.write_bytes(b"full_movie")

    manager = StreamCacheManager()
    manager._file_info["chat1_1"] = {"path": m1, "size": 10}

    purged = manager.startup_purge(wipe_all=True)
    assert purged == 3
    assert not b1.exists()
    assert not s1.exists()
    assert not m1.exists()
    assert len(manager._file_info) == 0


def test_emergency_disk_guard_eviction(tmp_path, monkeypatch):
    cache_dir = tmp_path / "tg_cache"
    blocks_dir = cache_dir / "blocks"
    cache_dir.mkdir(parents=True, exist_ok=True)
    blocks_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr("app.services.stream_cache.CACHE_DIR", cache_dir)
    monkeypatch.setattr("app.services.stream_cache.BLOCKS_DIR", blocks_dir)
    monkeypatch.setattr("app.services.stream_cache.MIN_FREE_DISK_BYTES", 2 * 1024 * 1024 * 1024)
    monkeypatch.setattr("app.services.stream_cache.MAX_CACHE_SIZE_BYTES", 1000)

    b1 = blocks_dir / "old_block.bin"
    b1.write_bytes(b"A" * 600)
    b2 = blocks_dir / "new_block.bin"
    b2.write_bytes(b"B" * 600)

    os.utime(b1, (1000, 1000))
    os.utime(b2, (2000, 2000))

    manager = StreamCacheManager()
    evicted = manager.check_disk_guard()
    assert evicted is True
    assert not b1.exists()
    assert b2.exists()


@pytest.mark.asyncio
async def test_stream_media_with_relay_forwarding(monkeypatch):
    from app.services.stream_engine import StreamEngine
    from app.services.stream_buffer import stream_buffer
    from app.services.stream_cache import stream_cache_service

    engine = StreamEngine()
    source_cid = "user_private_chat"
    source_mid = 101
    relay_cid = -100999999999
    relay_mid = 202

    chunk_size = 1024 * 1024
    payload = b"Z" * chunk_size

    norm_relay_cid = str(relay_cid).lstrip("-").replace("100", "", 1)
    stream_buffer.clear()
    stream_cache_service.put_slice(norm_relay_cid, relay_mid, 0, payload)

    monkeypatch.setattr(TelegramClientManager, "get_relay_channel_id", lambda: relay_cid)
    monkeypatch.setattr(TelegramClientManager, "forward_to_relay_channel", AsyncMock(return_value=relay_mid))

    mock_client = MagicMock()
    mock_client.session = MagicMock(dc_id=5, user_id="111")
    mock_client.is_connected = MagicMock(return_value=True)

    mock_relay_msg = MagicMock()
    mock_relay_msg.media = MagicMock()
    mock_get_fresh = AsyncMock(return_value=mock_relay_msg)

    chunks = []
    async for c in engine.stream_media(
        client=mock_client,
        chat_id=source_cid,
        message_id=source_mid,
        start=0,
        length=chunk_size,
        file_size=50 * 1024 * 1024,
        clean_name="test_relay_stream",
        get_fresh_message_fn=mock_get_fresh
    ):
        chunks.append(c)

    assert len(b"".join(chunks)) == chunk_size
    assert b"".join(chunks) == payload


