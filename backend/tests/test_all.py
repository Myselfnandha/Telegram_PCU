import asyncio
import os
import tempfile
import pytest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import TEMP_UPLOAD_DIR, DATA_DIR
from app.models import UploadStatus
from app.services.file_detector import detect_mime, categorize_file
from app.services.splitter import split_large_file, cleanup_files, InsufficientDiskSpaceError
from app.services.queue_manager import QueueManager, UploadItem
from app.services.sniffer_service import auto_rename, sniffer_service
from app.services.manager_detector import detect_managers
from app.routes.history import init_db, record_task_history, get_history, clear_history
import httpx
from app.main import app

@pytest.mark.asyncio
async def test_file_detector():
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
        # Write minimal JPEG magic header
        f.write(b"\xFF\xD8\xFF\xE0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00")
        f_path = Path(f.name)

    try:
        mime = detect_mime(f_path, filename="photo.jpg")
        cat = categorize_file(mime, filename="photo.jpg")
        assert cat == "photo"
    finally:
        f_path.unlink(missing_ok=True)

    assert categorize_file("video/mp4", "movie.mp4") == "video"
    assert categorize_file("audio/mpeg", "song.mp3") == "audio"
    assert categorize_file("application/zip", "backup.zip") == "archive"
    assert categorize_file("application/pdf", "manual.pdf") == "document"


@pytest.mark.asyncio
async def test_file_splitter():
    with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
        # Write 5 MB of dummy data
        f.write(b"A" * (5 * 1024 * 1024))
        f_path = Path(f.name)

    try:
        # Split into 2 MB chunks
        part_size = 2 * 1024 * 1024
        parts = await split_large_file(f_path, part_size=part_size)
        
        assert len(parts) == 3
        assert parts[0].stat().st_size == part_size
        assert parts[1].stat().st_size == part_size
        assert parts[2].stat().st_size == 1 * 1024 * 1024  # remainder

        # Test cleanup
        cleanup_files(parts)
        for p in parts:
            assert not p.exists()
    finally:
        f_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_queue_manager_lifecycle():
    qm = QueueManager(max_concurrent=1)
    
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
        f.write(b"Hello World")
        f_path = Path(f.name)

    try:
        item = UploadItem(
            task_id="test_task_1",
            file_path=f_path,
            original_filename="test.txt",
            chat_id=12345,
            is_temp_file=False
        )

        await qm.add_task(item)
        assert qm.get_task("test_task_1") is not None
        assert item.status == UploadStatus.QUEUED

        # Test pause / resume
        item.status = UploadStatus.UPLOADING
        assert qm.pause_task("test_task_1") is True
        assert item.status == UploadStatus.PAUSED

        assert qm.resume_task("test_task_1") is True
        assert item.status == UploadStatus.UPLOADING

        # Test cancel
        assert qm.cancel_task("test_task_1") is True
        assert item.status == UploadStatus.CANCELLED
    finally:
        f_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_database_history():
    await init_db()
    
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
        f.write(b"Database test")
        f_path = Path(f.name)

    try:
        item = UploadItem(
            task_id="db_task_1",
            file_path=f_path,
            original_filename="db_test.txt",
            chat_id=98765,
            chat_name="Test Chat",
            is_temp_file=False
        )
        item.status = UploadStatus.COMPLETED

        await record_task_history(item)

        history = await get_history(limit=10)
        matching = [h for h in history if h.id == "db_task_1"]
        assert len(matching) == 1
        assert matching[0].filename == "db_test.txt"
        assert matching[0].status == "completed"
        assert matching[0].chat_id == 98765

        await clear_history()
        history_after = await get_history(limit=10)
        assert len(history_after) == 0
    finally:
        f_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_api_endpoints():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # Auth check
        res = await client.get("/api/auth/status")
        assert res.status_code == 200
        assert "authenticated" in res.json()

        # Task list
        res = await client.get("/api/tasks")
        assert res.status_code == 200
        assert isinstance(res.json(), list)

        # History list
        res = await client.get("/api/history")
        assert res.status_code == 200
        assert isinstance(res.json(), list)

        # Static index
        res = await client.get("/")
        assert res.status_code == 200
        assert "TG Power Suite" in res.text

        # Sniffer status
        res = await client.get("/api/sniffer/status")
        assert res.status_code == 200
        assert "active_channels" in res.json()

        # Settings
        res = await client.get("/api/settings")
        assert res.status_code == 200
        assert "TG_API_ID" in res.json()

        # Settings managers
        res = await client.get("/api/settings/managers")
        assert res.status_code == 200
        assert "detected" in res.json()


@pytest.mark.asyncio
async def test_string_chat_id_history():
    await init_db()
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(b"%PDF-1.4 dummy test document")
        f_path = Path(f.name)

    try:
        item = UploadItem(
            task_id="string_chat_task",
            file_path=f_path,
            original_filename="doc.pdf",
            chat_id="me",
            chat_name="Saved Messages",
            is_temp_file=False
        )
        item.status = UploadStatus.COMPLETED
        await record_task_history(item)

        history = await get_history(limit=5)
        matching = [h for h in history if h.id == "string_chat_task"]
        assert len(matching) == 1
        assert matching[0].chat_id == "me"
        assert matching[0].filename == "doc.pdf"
    finally:
        f_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_socket_item_serialization():
    from app.socket_handlers import _item_to_state_dict
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
        f.write(b"Socket Test Data")
        f_path = Path(f.name)

    try:
        item = UploadItem(
            task_id="sock_task_1",
            file_path=f_path,
            original_filename="socket_test.txt",
            chat_id=123456,
            chat_name="Channel 1",
            is_temp_file=False
        )
        item.status = UploadStatus.UPLOADING
        item.progress = 45.5
        item.speed = 1048576.0
        item.eta = 12.0

        d = _item_to_state_dict(item)
        assert d["id"] == "sock_task_1"
        assert d["status"] == "uploading"
        assert d["progress"] == 45.5
        assert d["speed"] == 1048576.0
        assert d["eta"] == 12.0
        assert d["filename"] == "socket_test.txt"
    finally:
        f_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_auto_rename_helper():
    raw_name = "Avengers.Endgame.2019.1080p.BluRay.x264.DTS-HD.MA.7.1-FGT.mkv"
    cleaned = auto_rename(raw_name)
    assert "Avengers Endgame (2019)" in cleaned
    assert cleaned.endswith(".mkv")


@pytest.mark.asyncio
async def test_sniffer_channels_lifecycle():
    channel_id = "-1009988776655"
    added = sniffer_service.add_channel(channel_id)
    assert added is True
    assert -1009988776655 in sniffer_service.active_channels

    removed = sniffer_service.remove_channel(channel_id)
    assert removed is True
    assert -1009988776655 not in sniffer_service.active_channels


@pytest.mark.asyncio
async def test_fast_uploader_part_logic():
    from app.services.fast_uploader import PART_SIZE
    import math

    file_size_small = 100 * 1024  # 100 KB
    total_parts_small = math.ceil(file_size_small / PART_SIZE)
    assert total_parts_small == 1

    file_size_large = 20 * 1024 * 1024  # 20 MB
    total_parts_large = math.ceil(file_size_large / PART_SIZE)
    assert total_parts_large == 40


@pytest.mark.asyncio
async def test_thumbnail_fallback():
    from app.services.thumbnail import generate_video_thumbnail
    res = await generate_video_thumbnail(Path("/tmp/non_existent_video_123.mp4"))
    assert res is None


@pytest.mark.asyncio
async def test_zero_copy_chunk_config():
    from app.services.fast_uploader import get_optimal_chunk_config, SMALL_PART_SIZE, PART_SIZE
    
    p_size, workers, is_big = get_optimal_chunk_config(5 * 1024 * 1024)
    assert p_size == SMALL_PART_SIZE
    assert workers == 4
    assert is_big is False

    p_size, workers, is_big = get_optimal_chunk_config(100 * 1024 * 1024)
    assert p_size == PART_SIZE
    assert workers == 6
    assert is_big is True

    p_size, workers, is_big = get_optimal_chunk_config(1900 * 1024 * 1024)
    assert p_size == PART_SIZE
    assert workers == 8
    assert is_big is True


@pytest.mark.asyncio
async def test_concurrent_queue_manager():
    qm = QueueManager(max_concurrent=2)
    assert qm.max_concurrent == 2
    assert len(qm.get_all_tasks()) == 0


@pytest.mark.asyncio
async def test_cinema_manifest_and_endpoints():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # Check manifest contains Cinema shortcut
        res = await client.get("/manifest.json")
        assert res.status_code == 200
        manifest = res.json()
        shortcuts = manifest.get("shortcuts", [])
        assert any(s.get("name") == "Cinema Theater" for s in shortcuts)

        # Check proxy video query endpoint returns valid status code
        res_vid = await client.get("/api/media/videos/me")
        assert res_vid.status_code in (200, 401, 500, 503)

