import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from unittest.mock import patch, MagicMock
from app.services.player_launcher import PlayerLauncher, get_free_port
from app.services.playback_tracker import playback_tracker


def test_get_free_port():
    port = get_free_port(8089)
    assert isinstance(port, int)
    assert 1024 < port < 65535


def test_player_launcher_vlc_telemetry_flags_and_resume():
    launcher = PlayerLauncher()

    # Seed initial progress in tracker
    playback_tracker.update_progress(
        chat_id="test_chat",
        message_id=999,
        filename="telemetry_test.mkv",
        last_position_seconds=350.0,
        duration_seconds=1200.0
    )

    with patch("shutil.which", return_value="/usr/bin/vlc"), \
         patch("os.path.exists", return_value=True), \
         patch("subprocess.Popen") as mock_popen, \
         patch("app.services.subtitle_service.subtitle_service.get_cached_subtitle", return_value=None):

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc

        # Launch without explicit resume_sec -> should automatically pull 350s from tracker
        res = launcher.launch(
            stream_url="http://127.0.0.1:8088/dl/test_chat/999/telemetry_test.mkv",
            filename="telemetry_test.mkv",
            player="vlc",
            chat_id="test_chat",
            message_id=999
        )

        assert res["success"] is True
        assert res["launched"] is True
        args = mock_popen.call_args[0][0]

        # Verify VLC telemetry and fast-seek args
        assert "--extraintf=http" in args
        assert any(a.startswith("--http-port=") for a in args)
        assert "--http-password=tgsuite" in args
        assert "--start-time=350" in args
        assert "--input-fast-seek" in args

        # Verify Clean Bitstream Audio & Adaptive Jitter Safety (No crackle/desync/clipping)
        assert "--clock-jitter=0" not in args
        assert "--clock-synchro=0" not in args
        assert "--audio-filter=normvol,scaletempo" not in args
        assert "--drop-late-frames" not in args
        assert "--skip-frames" not in args
        assert "--audio-language=tam,eng,hin,tel,mal,kan,all" in args
        assert "--deinterlace=-1" in args
        assert "--deinterlace-mode=auto" in args

        # Verify Enhancement 2: Responsive 1000ms Caching & Keep-Alive
        assert "--network-caching=1000" in args
        assert "--file-caching=1000" in args
        assert "--http-reconnect" in args

        # Verify Enhancement 3: Network Subtitle Auto-Loading
        assert any(a.startswith("--sub-file=http://127.0.0.1:") or a.startswith("--input-slave=http://127.0.0.1:") for a in args)


def test_vlc_batch_binge_launch_and_playlists():
    launcher = PlayerLauncher()
    items = [
        {"chat_id": "123", "message_id": 101, "filename": "Ep01.mkv"},
        {"chat_id": "123", "message_id": 102, "filename": "Ep02.mkv"}
    ]

    with patch("shutil.which", return_value="/usr/bin/vlc"), \
         patch("os.path.exists", return_value=True), \
         patch("subprocess.Popen") as mock_popen:

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc

        # Verify batch binge launch
        res = launcher.launch_batch(items=items, title="Test Season", player="vlc", host="localhost:8088")
        assert res["success"] is True
        assert res["launched"] is True
        assert res["episodes_count"] == 2

        args = mock_popen.call_args[0][0]
        assert "--extraintf=http" in args
        assert "--clock-jitter=0" not in args
        assert "--clock-synchro=0" not in args
        assert "--audio-filter=normvol,scaletempo" not in args
        assert "--deinterlace=-1" in args
        assert "--network-caching=1000" in args
        assert "--http-reconnect" in args

    # Verify M3U playlist directives (Enhancements 1, 2, 3, Seeking)
    single_m3u = launcher.build_single_playlist("Movie.mkv", "http://localhost:8088/dl/1/2/Movie.mkv", "http://localhost:8088/sub.srt")
    assert "#EXTVLCOPT:audio-filter=normvol,scaletempo" not in single_m3u
    assert "#EXTVLCOPT:clock-jitter=0" not in single_m3u
    assert "#EXTVLCOPT:deinterlace=-1" in single_m3u
    assert "#EXTVLCOPT:network-caching=1000" in single_m3u
    assert "#EXTVLCOPT:http-reconnect" in single_m3u
    assert "#EXTVLCOPT:sub-file=http://localhost:8088/sub.srt" in single_m3u

    batch_m3u = launcher.build_playlist(items, "localhost:8088")
    assert "#EXTVLCOPT:audio-filter=normvol,scaletempo" not in batch_m3u
    assert "#EXTVLCOPT:clock-jitter=0" not in batch_m3u
    assert "#EXTVLCOPT:deinterlace=-1" in batch_m3u
    assert "#EXTVLCOPT:network-caching=1000" in batch_m3u
    assert "#EXTVLCOPT:http-reconnect" in batch_m3u


def test_vlc_live_remote_control_methods():
    launcher = PlayerLauncher()
    launcher._vlc_port = 59999
    # Inactive state returns active: False
    status = launcher.get_vlc_live_status()
    assert status["active"] is False

    # Stop command safely succeeds even when player is already closed
    stop_res = launcher.send_vlc_control("pl_stop")
    assert stop_res["success"] is True
    assert stop_res["state"] == "stopped"

    # Test seek command url formatting with mock response
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b'{"state": "playing", "time": 150, "length": 1800, "volume": 256, "fullscreen": false}'
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        seek_res = launcher.send_vlc_control("seek", "+30s")
        assert seek_res["success"] is True
        assert seek_res["time"] == 150
        assert seek_res["length"] == 1800
        req = mock_urlopen.call_args[0][0]
        assert "command=seek&val=%2B30s" in req.full_url


