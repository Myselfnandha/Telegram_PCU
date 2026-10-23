import sys
import asyncio
import pytest
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.stream_buffer import StreamSlidingBuffer
from app.services.player_launcher import PlayerLauncher
from app.services.stream_engine import StreamEngine


def test_stream_buffer_prefix_and_tail_slicing():
    """Verifies that StreamSlidingBuffer caches headers and tails accurately for 0ms replay."""
    buf = StreamSlidingBuffer()
    
    # 1. Push header data at offset 0
    header_data = b"HEAD" * 1024  # 4096 bytes
    buf.push("123", 456, 0, header_data)
    
    slice1 = buf.get_slice("123", 456, 0, 100)
    assert slice1 == header_data[:100]
    
    slice2 = buf.get_slice("123", 456, 100, 200)
    assert slice2 == header_data[100:300]
    
    # 2. Push tail metadata at offset 1,000,000
    tail_data = b"TAIL_METADATA_CUES"
    buf.push_tail("123", 456, 1000000, tail_data)
    
    tail_slice = buf.get_slice("123", 456, 1000000, len(tail_data))
    assert tail_slice == tail_data
    
    # 3. Clear resets everything
    buf.clear()
    assert buf.get_slice("123", 456, 0, 100) is None
    assert buf.get_slice("123", 456, 1000000, 10) is None


def test_player_launcher_vlc_command_construction():
    """Verifies that PlayerLauncher constructs optimal playback flags and manages processes."""
    launcher = PlayerLauncher()
    
    with patch("shutil.which", return_value="/usr/bin/vlc"), \
         patch("os.path.exists", return_value=True), \
         patch("subprocess.Popen") as mock_popen, \
         patch("app.services.subtitle_service.subtitle_service.get_cached_subtitle", return_value=Path("/tmp/test.srt")):
        
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc
        
        res = launcher.launch(
            stream_url="http://127.0.0.1:8088/dl/123/456/test.mkv",
            filename="test.mkv",
            player="vlc",
            resume_sec=120,
            chat_id=123,
            message_id=456
        )
        
        assert res["success"] is True
        assert res["launched"] is True
        assert res["player"] == "vlc"
        assert res["resume_seconds"] == 120
        
        # Verify CLI args passed to Popen
        args = mock_popen.call_args[0][0]
        assert "/usr/bin/vlc" in args[0]
        assert "--input-fast-seek" in args
        assert "--start-time=120" in args
        assert "--sub-track=0" in args
        assert "--one-instance" in args


@pytest.mark.asyncio
async def test_stream_engine_delivers_cached_prefix_immediately():
    """Verifies that StreamEngine delivers pre-cached RAM prefix in first yield without network call."""
    engine = StreamEngine()
    fake_client = MagicMock()
    fake_prefix = b"INSTANT_RAM_DATA" * 64
    
    chunks = []
    async for chunk in engine.stream_media(
        client=fake_client,
        chat_id="123",
        message_id=456,
        start=0,
        length=len(fake_prefix),
        file_size=len(fake_prefix),
        cached_prefix=fake_prefix
    ):
        chunks.append(chunk)
        
    assert b"".join(chunks) == fake_prefix


@pytest.mark.asyncio
async def test_cinema_catalog_caching_and_resolution():
    """Verifies that CinemaCatalog handles message resolution, entity priming, and caching."""
    from app.services.cinema_catalog import CinemaCatalog

    catalog = CinemaCatalog()
    fake_client = MagicMock()
    fake_msg = MagicMock()
    fake_msg.id = 777
    fake_msg.media = MagicMock()
    fake_client.get_messages = AsyncMock(return_value=fake_msg)

    # 1. First fetch hits client
    res = await catalog.get_streamable_message(fake_client, "12345", 777)
    assert res == fake_msg
    fake_client.get_messages.assert_awaited_once()

    # 2. Second fetch hits memory cache without client call
    fake_client.get_messages.reset_mock()
    res2 = await catalog.get_streamable_message(fake_client, "12345", 777)
    assert res2 == fake_msg
    fake_client.get_messages.assert_not_awaited()


def test_player_launcher_batch_and_playlist():
    """Verifies PlayerLauncher batch binge execution and M3U playlist generation."""
    launcher = PlayerLauncher()

    items = [
        {"chat_id": "111", "message_id": 1, "filename": "Ep01.mkv"},
        {"chat_id": "111", "message_id": 2, "filename": "Ep02.mkv"}
    ]

    # Test single playlist
    single_m3u = launcher.build_single_playlist("Movie.mkv", "http://127.0.0.1:8088/dl/1/2/Movie.mkv")
    assert "#EXTM3U" in single_m3u
    assert "#EXTVLCOPT:input-fast-seek" in single_m3u
    assert "Movie.mkv" in single_m3u

    # Test multi-item playlist
    batch_m3u = launcher.build_playlist(items, "127.0.0.1:8088", "Season_1")
    assert "#EXTM3U" in batch_m3u
    assert "Ep01.mkv" in batch_m3u
    assert "Ep02.mkv" in batch_m3u
    assert "http://127.0.0.1:8088/dl/111/1/Ep01.mkv" in batch_m3u
