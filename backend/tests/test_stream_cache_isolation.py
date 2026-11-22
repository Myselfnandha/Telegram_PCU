import os
import uuid
import pytest
from pathlib import Path
from app.services.stream_cache import StreamCacheManager, CACHE_DIR

@pytest.fixture
def cache_mgr():
    return StreamCacheManager.get_instance()


def test_cache_id_sanitization_and_traversal_neutralization(cache_mgr):
    """
    Verifies that malicious chat_ids or media identifiers attempting path traversal
    (e.g., '../../etc/passwd', '../secret') are strictly neutralized to safe filenames
    and remain strictly confined within the cache directory.
    """
    traversal_chat_id = "../../etc"
    traversal_media_id = "passwd"

    cache_path = cache_mgr.get_cache_path(traversal_chat_id, traversal_media_id)
    assert cache_path.is_relative_to(CACHE_DIR), "Cache path escaped cache directory!"
    assert ".." not in cache_path.name
    assert "______etc" in cache_path.name

    block_path = cache_mgr.get_block_path(traversal_chat_id, traversal_media_id, 0)
    assert block_path.is_relative_to(CACHE_DIR), "Block path escaped cache directory!"
    assert ".." not in block_path.name


def test_tenant_cache_slice_isolation(cache_mgr):
    """
    Verifies that cache slices stored by User A cannot be accessed or overwritten
    by User B even when requesting identical message IDs under different tenant contexts.
    """
    tenant_a_prefix = f"tenant_{uuid.uuid4().hex[:6]}"
    tenant_b_prefix = f"tenant_{uuid.uuid4().hex[:6]}"
    media_id = 999888

    # User A puts a slice
    slice_data_a = b"USER_A_PRIVATE_MEDIA_STREAM_DATA_00000000"
    cache_mgr.put_slice(tenant_a_prefix, media_id, 0, slice_data_a)

    # User B puts a slice with same media_id
    slice_data_b = b"USER_B_DIFFERENT_MEDIA_CONTENT_111111111"
    cache_mgr.put_slice(tenant_b_prefix, media_id, 0, slice_data_b)

    # Verify retrieval isolation
    retrieved_a = cache_mgr.get_slice(tenant_a_prefix, media_id, 0, len(slice_data_a))
    retrieved_b = cache_mgr.get_slice(tenant_b_prefix, media_id, 0, len(slice_data_b))

    assert retrieved_a == slice_data_a
    assert retrieved_b == slice_data_b
    assert retrieved_a != retrieved_b


def test_cache_block_path_structure(cache_mgr):
    """
    Verifies discrete 10MB block path calculation and ensures each block is uniquely addressable.
    """
    chat_id = "my_channel"
    msg_id = 12345
    block_0 = cache_mgr.get_block_path(chat_id, msg_id, 0)
    block_1 = cache_mgr.get_block_path(chat_id, msg_id, 1)

    assert block_0 != block_1
    assert block_0.is_relative_to(CACHE_DIR)
    assert block_1.is_relative_to(CACHE_DIR)
    assert "part00000.bin" in block_0.name
    assert "part00001.bin" in block_1.name
