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

        # Launch WITH resume=False -> should start at 00:00:00 with NO --start-time flag
        res_fresh = launcher.launch(
            stream_url="http://127.0.0.1:8088/dl/test_chat/999/telemetry_test.mkv",
            filename="telemetry_test.mkv",
            player="vlc",
            chat_id="test_chat",
            message_id=999,
            resume=False
        )
        assert res_fresh["success"] is True
        args_fresh = mock_popen.call_args[0][0]
        assert not any(a.startswith("--start-time=") for a in args_fresh)

        # Launch WITH explicit resume_sec=0 -> should also not have --start-time flag
        res_zero = launcher.launch(
            stream_url="http://127.0.0.1:8088/dl/test_chat/999/telemetry_test.mkv",
            filename="telemetry_test.mkv",
            player="vlc",
            resume_sec=0,
            chat_id="test_chat",
            message_id=999
        )
        assert res_zero["success"] is True
        args_zero = mock_popen.call_args[0][0]
        assert not any(a.startswith("--start-time=") for a in args_zero)


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


def test_vlc_single_launch_with_auto_queued_episodes():
    launcher = PlayerLauncher()
    items = [
        {"chat_id": "100", "message_id": 1, "filename": "S01E01.mkv"},
        {"chat_id": "100", "message_id": 2, "filename": "S01E02.mkv"},
        {"chat_id": "100", "message_id": 3, "filename": "S01E03.mkv"},
    ]

    with patch("shutil.which", return_value="/usr/bin/vlc"), \
         patch("os.path.exists", return_value=True), \
         patch("subprocess.Popen") as mock_popen, \
         patch("app.services.subtitle_service.subtitle_service.get_cached_subtitle", return_value=None):

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc

        res = launcher.launch(
            stream_url="http://127.0.0.1:8088/dl/100/1/S01E01.mkv",
            filename="S01E01.mkv",
            player="vlc",
            chat_id="100",
            message_id=1,
            items=items
        )

        assert res["success"] is True
        args = mock_popen.call_args[0][0]
        # Current stream is first
        assert "http://127.0.0.1:8088/dl/100/1/S01E01.mkv" in args
        # Subsequent episodes are auto-queued in VLC command args
        assert any("100/2/S01E02.mkv" in a for a in args)
        assert any("100/3/S01E03.mkv" in a for a in args)
        assert launcher._active_media_info.get("items") == items


def test_vlc_remote_control_track_switching_and_delays():
    launcher = PlayerLauncher()
    launcher._vlc_port = 8089

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b'{"state": "playing", "time": 100, "length": 2000, "volume": 320, "fullscreen": true, "audiodelay": 0.05, "subtitledelay": -0.25, "aspectratio": "16:9"}'
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        # 1. Audio track
        res1 = launcher.send_vlc_control("audio_track", 2)
        assert res1["success"] is True
        assert "command=audio_track&val=2" in mock_urlopen.call_args[0][0].full_url

        # 2. Subtitle track
        res2 = launcher.send_vlc_control("subtitle_track", 1)
        assert res2["success"] is True
        assert "command=subtitle_track&val=1" in mock_urlopen.call_args[0][0].full_url

        # 3. Audio delay
        res3 = launcher.send_vlc_control("audiodelay", "+0.05")
        assert res3["success"] is True
        assert "command=audiodelay" in mock_urlopen.call_args[0][0].full_url
        assert res3["audiodelay"] == 0.05

        # 4. Aspect ratio alias normalization
        res4 = launcher.send_vlc_control("aspect_ratio", "16:9")
        assert res4["success"] is True
        assert "command=aspectratio&val=16%3A9" in mock_urlopen.call_args[0][0].full_url


def test_vlc_live_status_parsing_extended():
    launcher = PlayerLauncher()
    launcher._vlc_port = 8089
    launcher._active_media_info = {
        "chat_id": "100",
        "message_id": 1,
        "filename": "S01E01.mkv",
        "duration": 2400,
        "items": [
            {"chat_id": "100", "message_id": 1, "filename": "S01E01.mkv"},
            {"chat_id": "100", "message_id": 2, "filename": "S01E02.mkv"},
        ]
    }

    mock_vlc_json = {
        "state": "playing",
        "time": 250,
        "length": 2400,
        "volume": 384,  # 150% volume
        "fullscreen": False,
        "rate": 1.25,
        "audiodelay": 0.05,
        "subtitledelay": -0.25,
        "aspectratio": "16:9",
        "information": {
            "category": {
                "meta": {"filename": "S01E01.mkv"},
                "Stream 0": {"Type": "Video", "Codec": "H264", "Resolution": "1920x1080"},
                "Stream 1": {"Type": "Audio", "Language": "English", "Codec": "aac", "Channels": "Stereo"},
                "Stream 2": {"Type": "Audio", "Language": "Tamil", "Codec": "ac3", "Channels": "5.1"},
                "Stream 3": {"Type": "Subtitle", "Language": "English", "Codec": "subrip"},
            }
        }
    }

    import json
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps(mock_vlc_json).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        status = launcher.get_vlc_live_status()
        assert status["active"] is True
        assert status["volume_percent"] == 150
        assert status["rate"] == 1.25
        assert status["audiodelay"] == 0.05
        assert status["subtitledelay"] == -0.25
        assert status["aspectratio"] == "16:9"
        assert status["episode_index"] == 1
        assert status["total_episodes"] == 2
        assert status["next_episode"] == "S01E02.mkv"
        assert len(status["audio_tracks"]) == 2
        assert status["audio_tracks"][0]["language"] == "English"
        assert status["audio_tracks"][1]["language"] == "Tamil"
        assert len(status["subtitle_tracks"]) == 1
        assert status["subtitle_tracks"][0]["language"] == "English"
        assert status["video_info"] is not None
        assert status["video_info"]["codec"] == "H264"
        assert status["video_info"]["resolution"] == "1920x1080"


def test_proxy_vlc_dedicated_rest_endpoints():
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)

    with patch.object(PlayerLauncher, "send_vlc_control", return_value={"success": True, "state": "playing"}):
        # 1. Track endpoint
        r1 = client.post("/api/media/vlc/track", json={"type": "audio", "track_id": 2})
        assert r1.status_code == 200
        assert r1.json()["success"] is True

        r2 = client.post("/api/media/vlc/track", json={"type": "subtitle", "track_id": 1})
        assert r2.status_code == 200
        assert r2.json()["success"] is True

        # 2. Sync endpoint
        r3 = client.post("/api/media/vlc/sync", json={"type": "audio", "delta_seconds": 0.05})
        assert r3.status_code == 200
        assert r3.json()["success"] is True

        # 3. Aspect ratio endpoint
        r4 = client.post("/api/media/vlc/aspect_ratio", json={"ratio": "16:9"})
        assert r4.status_code == 200
        assert r4.json()["success"] is True

        # 4. Volume endpoint
        r5 = client.post("/api/media/vlc/volume", json={"volume_percent": 150})
        assert r5.status_code == 200
        assert r5.json()["success"] is True



