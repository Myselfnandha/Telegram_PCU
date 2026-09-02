"""
Auto-Fetch Subtitles Service for VLC & Desktop Players.
Parses movie/series metadata and automatically downloads matching .srt subtitles.
"""

import os
import re
import gzip
import json
import logging
import asyncio
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional, Dict, Any, Tuple

from app.config import DATA_DIR

logger = logging.getLogger("subtitle_service")

SUBTITLES_DIR = DATA_DIR / "subtitles"
SUBTITLES_DIR.mkdir(parents=True, exist_ok=True)


class SubtitleService:
    def __init__(self):
        SUBTITLES_DIR.mkdir(parents=True, exist_ok=True)

    def parse_media_title(self, filename: str) -> Dict[str, Any]:
        """
        Extracts cleaned title, year, season, and episode from raw media filename.
        e.g. 'Inception.2010.1080p.BluRay.x264.mkv' -> { 'title': 'Inception', 'year': 2010, 'is_series': False }
        e.g. 'Stranger.Things.S04E07.1080p.WEB-DL.mkv' -> { 'title': 'Stranger Things', 'season': 4, 'episode': 7, 'is_series': True }
        """
        clean = filename
        # Strip extension
        clean = os.path.splitext(clean)[0]
        # Replace dots, underscores with spaces
        clean = re.sub(r"[._]", " ", clean).strip()

        # Check for Series Season / Episode: S01E02, 1x02, Season 1 Episode 2
        series_match = re.search(r"(.*?)\s*(?:[sS](\d{1,2})[eE](\d{1,2})|(\d{1,2})[xX](\d{1,2})|[sS]eason\s*(\d{1,2})\s*[eE]pisode\s*(\d{1,2}))", clean, re.IGNORECASE)
        if series_match:
            raw_title = series_match.group(1).strip()
            s_num = int(series_match.group(2) or series_match.group(4) or series_match.group(6) or 1)
            e_num = int(series_match.group(3) or series_match.group(5) or series_match.group(7) or 1)
            
            # Clean title
            title = re.sub(r"\b(720p|1080p|2160p|4k|bluray|web-dl|webrip|x264|x265|hevc|aac|dts|hdrip)\b.*", "", raw_title, flags=re.IGNORECASE).strip()
            return {
                "title": title or raw_title,
                "is_series": True,
                "season": s_num,
                "episode": e_num,
                "year": None,
                "query": f"{title} S{s_num:02d}E{e_num:02d}"
            }

        # Check for Movie Year (1900 - 2099)
        year_match = re.search(r"(.*?)\s*\(?((?:19|20)\d{2})\)?", clean)
        if year_match:
            raw_title = year_match.group(1).strip()
            year = int(year_match.group(2))
            title = re.sub(r"\b(720p|1080p|2160p|4k|bluray|web-dl|webrip|x264|x265|hevc|aac|dts|hdrip)\b.*", "", raw_title, flags=re.IGNORECASE).strip()
            return {
                "title": title or raw_title,
                "is_series": False,
                "season": None,
                "episode": None,
                "year": year,
                "query": f"{title} {year}"
            }

        # Generic title
        title = re.sub(r"\b(720p|1080p|2160p|4k|bluray|web-dl|webrip|x264|x265|hevc|aac|dts|hdrip)\b.*", "", clean, flags=re.IGNORECASE).strip()
        return {
            "title": title or clean,
            "is_series": False,
            "season": None,
            "episode": None,
            "year": None,
            "query": title or clean
        }

    def get_cached_subtitle_path(self, filename: str, lang: str = "eng") -> Path:
        """Returns local path for cached subtitle."""
        clean_base = re.sub(r"[^\w\-_.]", "_", os.path.splitext(filename)[0])
        return SUBTITLES_DIR / f"{clean_base}.{lang}.srt"

    async def get_or_download_subtitle(self, filename: str, lang: str = "eng") -> Optional[Path]:
        """
        Returns path to matching .srt subtitle file.
        If not cached locally, attempts to search and fetch from public subtitle mirrors.
        """
        cached_sub = self.get_cached_subtitle_path(filename, lang)
        if cached_sub.exists() and cached_sub.stat().st_size > 100:
            return cached_sub

        meta = self.parse_media_title(filename)
        query = meta.get("query", filename)
        logger.info(f"Searching subtitles for query: '{query}' ({lang})...")

        try:
            # Run in thread pool to avoid blocking asyncio loop
            sub_path = await asyncio.to_thread(self._fetch_subtitle_sync, query, cached_sub, lang)
            if sub_path and sub_path.exists():
                logger.info(f"✅ Auto-fetched subtitle successfully: {sub_path.name}")
                return sub_path
        except Exception as e:
            logger.debug(f"Subtitle search notice for '{query}': {e}")

        return None

    def _fetch_subtitle_sync(self, query: str, target_file: Path, lang: str = "eng") -> Optional[Path]:
        """Synchronously queries OpenSubtitles / Subtitle search endpoint."""
        clean_query = urllib.parse.quote(query)
        # OpenSubtitles v1 user-agent API search endpoint
        url = f"https://rest.opensubtitles.org/search/query-{clean_query}/sublanguageid-{lang}"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "TGPowerSuite v3.4.0 (Mozilla/5.0)",
                "Accept": "application/json"
            }
        )

        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode("utf-8"))
                    if isinstance(data, list) and len(data) > 0:
                        # Pick top rating result
                        best = data[0]
                        download_url = best.get("SubDownloadLink")
                        if download_url:
                            # Download and unpack .gz
                            sub_req = urllib.request.Request(
                                download_url,
                                headers={"User-Agent": "TGPowerSuite v3.4.0"}
                            )
                            with urllib.request.urlopen(sub_req, timeout=8) as sub_res:
                                raw_bytes = sub_res.read()
                                # Handle gzip compressed payload
                                try:
                                    srt_content = gzip.decompress(raw_bytes)
                                except Exception:
                                    srt_content = raw_bytes

                                if srt_content and len(srt_content) > 50:
                                    with open(target_file, "wb") as f:
                                        f.write(srt_content)
                                    return target_file
        except Exception as e:
            logger.debug(f"OpenSubtitles API lookup skipped: {e}")

        return None


# Global singleton subtitle service
subtitle_service = SubtitleService()
