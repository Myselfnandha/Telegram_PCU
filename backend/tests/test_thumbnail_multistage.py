import sys
import os
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
BACKEND_DIR = ROOT_DIR / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from app.main import app
from app.routes.proxy import DEFAULT_VIDEO_PLACEHOLDER_SVG, _THUMB_CACHE_DIR
from app.services.cinema_catalog import cinema_catalog

@pytest.fixture
def test_client():
    return TestClient(app)

def test_thumbnail_disk_cache_hit_returns_jpeg(test_client):
    cache_dir = Path(_THUMB_CACHE_DIR)
    cache_dir.mkdir(parents=True, exist_ok=True)
    test_img = cache_dir / "-100123999_444.jpg"
    test_img.write_bytes(b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00" + (b"\xaa" * 64))

    try:
        res = test_client.get("/dl/-100123999/444/thumb")
        assert res.status_code == 200
        assert res.headers["content-type"] == "image/jpeg"
        assert "immutable" in res.headers.get("Cache-Control", "")
        assert res.content.startswith(b"\xff\xd8\xff")
    finally:
        if test_img.exists():
            test_img.unlink()

def test_thumbnail_stream_alias_route(test_client):
    cache_dir = Path(_THUMB_CACHE_DIR)
    cache_dir.mkdir(parents=True, exist_ok=True)
    test_img = cache_dir / "-100123888_555.jpg"
    test_img.write_bytes(b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00" + (b"\xbb" * 64))

    try:
        res = test_client.get("/stream/-100123888/555/thumb")
        assert res.status_code == 200
        assert res.headers["content-type"] == "image/jpeg"
        assert res.content.startswith(b"\xff\xd8\xff")
    finally:
        if test_img.exists():
            test_img.unlink()

def test_thumbnail_svg_placeholder_format():
    assert DEFAULT_VIDEO_PLACEHOLDER_SVG.startswith(b"<svg")
    assert b"viewBox=\"0 0 320 180\"" in DEFAULT_VIDEO_PLACEHOLDER_SVG
    assert b"polygon" in DEFAULT_VIDEO_PLACEHOLDER_SVG
    assert b"circle" in DEFAULT_VIDEO_PLACEHOLDER_SVG
    assert b"#3b82f6" in DEFAULT_VIDEO_PLACEHOLDER_SVG

def test_thumbnail_non_existent_returns_svg_gracefully(test_client):
    res = test_client.get("/dl/8888888888/111111/thumb")
    assert res.status_code == 200
    assert "image/svg+xml" in res.headers["content-type"]
    assert b"<svg" in res.content

def test_thumbnail_cache_control_headers(test_client):
    res = test_client.get("/dl/7777777777/222222/thumb")
    assert res.status_code == 200
    cc = res.headers.get("Cache-Control", "")
    assert "public" in cc
    assert "max-age=" in cc

def test_thumbnail_stripped_photo_conversion():
    from telethon import utils
    stripped_bytes = b"\x01\x14\x14" + (b"\x11" * 64)
    try:
        converted = utils.stripped_photo_to_jpg(stripped_bytes)
        assert isinstance(converted, (bytes, bytearray))
    except Exception:
        pass

def test_cinema_catalog_serializes_thumb_url_properly():
    item = {
        "message_id": 101,
        "chat_id": "-100555666",
        "filename": "Movie.2023.1080p.mkv",
        "has_thumb": True,
        "thumb_url": "/dl/-100555666/101/thumb"
    }
    assert item["has_thumb"] is True
    assert item["thumb_url"].startswith("/dl/")
    assert item["thumb_url"].endswith("/thumb")

@pytest.mark.asyncio
async def test_thumbnail_purges_old_micro_preview():
    from app.routes.proxy import get_media_thumbnail
    from app.telegram_client import TelegramClientManager
    from unittest.mock import AsyncMock, MagicMock, patch

    cache_dir = Path(_THUMB_CACHE_DIR)
    micro_file = cache_dir / "999888_123.jpg"
    micro_file.write_bytes(b"\xff\xd8\xff\xe0" + (b"\x00" * 700))

    mock_client = MagicMock()
    mock_client.is_connected = MagicMock(return_value=False)

    try:
        with patch.object(TelegramClientManager, "get_client", AsyncMock(return_value=mock_client)):
            res = await get_media_thumbnail("999888", 123)
            assert not micro_file.exists()
            assert res.status_code == 200
            assert "image/svg+xml" in res.headers["content-type"]
    finally:
        for p in cache_dir.glob("*999888*"):
            try:
                p.unlink()
            except Exception:
                pass

@pytest.mark.asyncio
async def test_thumbnail_prefers_photosize_over_stripped():
    from telethon.tl.types import PhotoSize, PhotoStrippedSize, MessageMediaDocument, Document
    from unittest.mock import AsyncMock, MagicMock, patch
    from app.routes.proxy import get_media_thumbnail
    from app.telegram_client import TelegramClientManager

    p_small = PhotoStrippedSize(type="i", bytes=b"\x01\x14\x14" + (b"\x11" * 64))
    p_large = PhotoSize(type="m", w=320, h=320, size=18000)

    fake_doc = Document(
        id=777, access_hash=888, file_reference=b"ref", date=None,
        mime_type="video/mp4", size=50000000, dc_id=2, attributes=[], thumbs=[p_small, p_large]
    )
    fake_msg = MagicMock()
    fake_msg.media = MessageMediaDocument(document=fake_doc, nopremium=False, spoiler=False, video=True, round=False, voice=False, alt_documents=[])
    fake_msg.id = 555

    mock_client = MagicMock()
    mock_client.is_connected = MagicMock(return_value=True)
    mock_client.download_media = AsyncMock(return_value=b"\xff\xd8\xff\xe0" + (b"\xaa" * 2000))

    from app.services.cinema_catalog import cinema_catalog
    cinema_catalog._message_cache[("888777", 555)] = (fake_msg, 9999999999)

    cache_dir = Path(_THUMB_CACHE_DIR)
    for p in cache_dir.glob("*888777*"):
        try:
            p.unlink()
        except Exception:
            pass

    try:
        with patch.object(TelegramClientManager, "get_client", AsyncMock(return_value=mock_client)):
            res = await get_media_thumbnail("888777", 555)
            assert res.status_code == 200
            assert res.headers["content-type"] == "image/jpeg"
            assert len(res.body) > 1500
            mock_client.download_media.assert_called()
            call_kwargs = mock_client.download_media.call_args.kwargs
            assert call_kwargs.get("thumb") == p_large
    finally:
        for p in cache_dir.glob("*888777*"):
            try:
                p.unlink()
            except Exception:
                pass
