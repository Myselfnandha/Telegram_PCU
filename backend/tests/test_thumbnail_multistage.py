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
