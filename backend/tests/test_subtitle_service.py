import os
import sys
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.subtitle_service import SubtitleService


def test_parse_movie_title():
    svc = SubtitleService()
    res = svc.parse_media_title("Interstellar.2014.1080p.BluRay.x264.DTS-HD.mkv")
    assert res["title"] == "Interstellar"
    assert res["year"] == 2014
    assert res["is_series"] is False
    assert res["query"] == "Interstellar 2014"


def test_parse_series_title():
    svc = SubtitleService()
    res = svc.parse_media_title("Breaking.Bad.S05E14.720p.HDTV.x264.mkv")
    assert res["title"] == "Breaking Bad"
    assert res["is_series"] is True
    assert res["season"] == 5
    assert res["episode"] == 14
    assert res["query"] == "Breaking Bad S05E14"


def test_convert_srt_to_vtt():
    svc = SubtitleService()
    srt_sample = (
        "1\n"
        "00:01:20,000 --> 00:01:23,500\n"
        "Hello, world!\n\n"
        "2\n"
        "00:01:24,100 --> 00:01:27,800\n"
        "Welcome to TG Power Suite.\n"
    )
    vtt = svc.convert_srt_to_vtt(srt_sample)
    assert vtt.startswith("WEBVTT")
    assert "00:01:20.000 --> 00:01:23.500" in vtt
    assert "00:01:24.100 --> 00:01:27.800" in vtt
    assert "Hello, world!" in vtt


def test_cached_subtitle_lookup(tmp_path):
    svc = SubtitleService()
    with patch("app.services.subtitle_service.SUBTITLES_DIR", tmp_path):
        dummy_srt = tmp_path / "Inception_2014.eng.srt"
        dummy_srt.write_text("1\n00:00:01,000 --> 00:00:05,000\nSub text\n")

        found = svc.get_cached_subtitle("Inception.2014.mkv", lang="eng")
        assert found is not None
        assert found.exists()
        assert found.name == "Inception_2014.eng.srt"


@pytest.mark.asyncio
async def test_extract_embedded_subtitles(tmp_path):
    svc = SubtitleService()
    with patch("app.services.subtitle_service.SUBTITLES_DIR", tmp_path):
        fake_probe_json = b'{"streams": [{"index": 2, "codec_name": "subrip", "tags": {"language": "tam", "title": "Tamil Sub"}}]}'

        mock_proc_probe = AsyncMock()
        mock_proc_probe.communicate.return_value = (fake_probe_json, b"")

        mock_proc_extract = AsyncMock()
        mock_proc_extract.wait.return_value = 0

        async def fake_create_subprocess_exec(*args, **kwargs):
            if args[0] == "ffprobe":
                return mock_proc_probe
            # ffmpeg command: create dummy output file
            out_file = Path(args[-1])
            out_file.write_text("1\n00:00:01,000 --> 00:00:04,000\nVanakkam\n")
            return mock_proc_extract

        with patch("asyncio.create_subprocess_exec", side_effect=fake_create_subprocess_exec):
            extracted = await svc.extract_embedded_subtitles("http://127.0.0.1:8088/dl/1/1", "Master.2021.mkv")
            assert "tam" in extracted
            assert extracted["tam"].exists()
            assert "Vanakkam" in extracted["tam"].read_text()
