import sys
from pathlib import Path
import pytest

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
BACKEND_DIR = ROOT_DIR / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from app.services.subtitle_service import SubtitleService

@pytest.fixture
def parser():
    return SubtitleService()

def test_movie_categorization_with_year(parser):
    res = parser.parse_media_title("Dune.2021.1080p.BluRay.x264.mkv")
    assert res["is_series"] is False
    assert res["title"] == "Dune"
    assert res["year"] == 2021
    assert res["season"] is None
    assert res["episode"] is None

def test_movie_file_bundling_subtitles_and_nfo(parser):
    video = parser.parse_media_title("Interstellar.2014.1080p.mkv")
    sub_eng = parser.parse_media_title("Interstellar.2014.eng.srt")
    sub_forced = parser.parse_media_title("Interstellar.2014.forced.vtt")
    info = parser.parse_media_title("Interstellar.2014.nfo")
    text = parser.parse_media_title("Interstellar.2014.readme.txt")

    assert video["title"] == "Interstellar"
    assert video["year"] == 2014
    assert sub_eng["title"] == "Interstellar"
    assert sub_eng["year"] == 2014
    assert sub_forced["title"] == "Interstellar"
    assert sub_forced["year"] == 2014
    assert info["title"] == "Interstellar"
    assert info["year"] == 2014
    assert text["title"] == "Interstellar"
    assert text["year"] == 2014

def test_movie_franchise_sequel_numbering(parser):
    p1 = parser.parse_media_title("John Wick (2014) 1080p.mkv")
    p2 = parser.parse_media_title("John Wick Chapter 2 (2017) 1080p.mkv")
    p3 = parser.parse_media_title("John Wick Chapter 3 Parabellum (2019) 1080p.mkv")
    p4 = parser.parse_media_title("John Wick Chapter 4 (2023) 2160p UHD.mkv")

    assert p1["year"] == 2014
    assert p2["year"] == 2017
    assert p3["year"] == 2019
    assert p4["year"] == 2023
    for p in [p1, p2, p3, p4]:
        assert "John Wick" in p["title"]
        assert p["is_series"] is False

def test_series_categorization_standard_s_e(parser):
    res = parser.parse_media_title("Breaking.Bad.S01E01.720p.mkv")
    assert res["is_series"] is True
    assert res["title"] == "Breaking Bad"
    assert res["season"] == 1
    assert res["episode"] == 1

def test_series_categorization_season_words(parser):
    res = parser.parse_media_title("Game.of.Thrones.Season.4.Episode.09.1080p.mkv")
    assert res["is_series"] is True
    assert res["title"] == "Game of Thrones"
    assert res["season"] == 4
    assert res["episode"] == 9

def test_series_categorization_x_notation(parser):
    res = parser.parse_media_title("Sherlock.03x02.The.Sign.of.Three.mkv")
    assert res["is_series"] is True
    assert res["title"] == "Sherlock"
    assert res["season"] == 3
    assert res["episode"] == 2

def test_series_single_episode_pilot_detection(parser):
    p1 = parser.parse_media_title("Chernobyl.EP01.1080p.mkv")
    assert p1["is_series"] is True
    assert p1["episode"] == 1

    p2 = parser.parse_media_title("True.Detective.Pilot.Episode.1.mkv")
    assert p2["is_series"] is True
    assert p2["episode"] == 1

def test_series_multi_episode_range(parser):
    res = parser.parse_media_title("Narcos.S01E01-E04.Pack.mkv")
    assert res["is_series"] is True
    assert res["title"] == "Narcos"
    assert res["season"] == 1
    assert res["episode"] == 1

def test_series_cleaning_channel_tags(parser):
    res = parser.parse_media_title("vip_linkz_Vikings.S06E11.720p.HDTV.mkv")
    assert res["is_series"] is True
    assert "Vikings" in res["title"]
    assert res["season"] == 6
    assert res["episode"] == 11
