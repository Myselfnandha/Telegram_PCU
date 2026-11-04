import sys
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.services.rust_bridge import RustBridge, rust_bridge

@pytest.mark.asyncio
async def test_rust_bridge_binary_detection():
    bridge = RustBridge()
    bin_path = bridge.find_binary()
    assert bin_path is not None
    assert bin_path.name == "tg-rust-core"
    assert bin_path.exists()

@pytest.mark.asyncio
async def test_rust_bridge_healthy_check():
    bridge = RustBridge()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"status": "ok", "engine": "Rust MTProto (grammers)", "active_accounts": 2, "rate_limit_mbs": 24.0}

    with patch.object(bridge._get_client(), "get", new=AsyncMock(return_value=mock_resp)):
        assert await bridge.is_healthy() is True
        status = await bridge.get_status()
        assert status["status"] == "ok"
        assert status["active_accounts"] == 2

@pytest.mark.asyncio
async def test_rust_bridge_session_registration():
    bridge = RustBridge()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"success": True, "active_accounts": 1}

    with patch.object(bridge._get_client(), "post", new=AsyncMock(return_value=mock_resp)) as mock_post:
        ok = await bridge.register_session("1AQAAAA=...", 12345)
        assert ok is True
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert args[0] == "/api/session/register"
        assert kwargs["json"]["session_string"] == "1AQAAAA=..."
        assert kwargs["json"]["api_id"] == 12345

@pytest.mark.asyncio
async def test_rust_bridge_stream_chunk_success():
    bridge = RustBridge()
    fake_chunk = b"X" * 1024
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = fake_chunk

    with patch.object(bridge._get_client(), "post", new=AsyncMock(return_value=mock_resp)) as mock_post:
        data = await bridge.stream_chunk(
            dc_id=5,
            doc_id=1001,
            access_hash=2002,
            file_reference=b"\x01\x02\x03\x04",
            offset=0,
            limit=1024,
            priority="high"
        )
        assert data == fake_chunk
        mock_post.assert_called_once()
        _, kwargs = mock_post.call_args
        assert kwargs["json"]["dc_id"] == 5
        assert kwargs["json"]["doc_id"] == 1001
        assert kwargs["json"]["file_reference_b64"] == "AQIDBA=="

@pytest.mark.asyncio
async def test_rust_bridge_stream_chunk_fallback_on_error():
    bridge = RustBridge()
    mock_resp = MagicMock()
    mock_resp.status_code = 503
    mock_resp.text = "FloodWait"

    with patch.object(bridge._get_client(), "post", new=AsyncMock(return_value=mock_resp)):
        data = await bridge.stream_chunk(
            dc_id=5,
            doc_id=1001,
            access_hash=2002,
            file_reference=b"\x01",
            offset=0,
            limit=512
        )
        assert data is None

@pytest.mark.asyncio
async def test_rust_bridge_upload_part():
    bridge = RustBridge()
    mock_resp = MagicMock()
    mock_resp.status_code = 200

    with patch.object(bridge._get_client(), "post", new=AsyncMock(return_value=mock_resp)) as mock_post:
        ok = await bridge.upload_part(
            file_id=5555,
            file_part=0,
            file_total_parts=4,
            chunk=b"HELLO_RUST_MTPROTO"
        )
        assert ok is True
        mock_post.assert_called_once()
        _, kwargs = mock_post.call_args
        assert kwargs["json"]["file_id"] == 5555
        assert kwargs["json"]["file_part"] == 0

@pytest.mark.asyncio
async def test_rust_bridge_report_flood():
    bridge = RustBridge()
    mock_resp = MagicMock()
    mock_resp.status_code = 200

    with patch.object(bridge._get_client(), "post", new=AsyncMock(return_value=mock_resp)) as mock_post:
        ok = await bridge.report_flood(account_idx=1, seconds=15)
        assert ok is True
        mock_post.assert_called_once()
        _, kwargs = mock_post.call_args
        assert kwargs["json"]["account_idx"] == 1
        assert kwargs["json"]["seconds"] == 15

@pytest.mark.asyncio
async def test_rust_bridge_circuit_breaker():
    bridge = RustBridge()
    mock_resp = MagicMock()
    mock_resp.status_code = 502
    mock_resp.text = "MTProto GetFile timed out after 5s"

    with patch.object(bridge._get_client(), "post", new=AsyncMock(return_value=mock_resp)) as mock_post:
        for _ in range(3):
            data = await bridge.stream_chunk(dc_id=4, doc_id=1, access_hash=2, file_reference=b"\x01", offset=0, limit=1024)
            assert data is None
        assert mock_post.call_count == 3

        mock_post.reset_mock()
        data = await bridge.stream_chunk(dc_id=4, doc_id=1, access_hash=2, file_reference=b"\x01", offset=0, limit=1024)
        assert data is None
        assert mock_post.call_count == 0

        bridge.reset_circuit()
        mock_resp.status_code = 200
        mock_resp.content = b"RECOVERED"
        data = await bridge.stream_chunk(dc_id=4, doc_id=1, access_hash=2, file_reference=b"\x01", offset=0, limit=1024)
        assert data == b"RECOVERED"
        assert mock_post.call_count == 1

