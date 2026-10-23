import os
import re
import gzip
import json
import logging
import asyncio
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional, Dict, Any, Tuple, List

from app.config import DATA_DIR, DEFAULT_SUBTITLE_LANG

logger = logging.getLogger("subtitle_service")

SUBTITLES_DIR = DATA_DIR / "subtitles"
SUBTITLES_DIR.mkdir(parents=True, exist_ok=True)


class SubtitleService:
    def __init__(self):
        SUBTITLES_DIR.mkdir(parents=True, exist_ok=True)
        self._active_prefetches: Dict[str, asyncio.Task] = {}

    def parse_media_title(self, filename: str) -> Dict[str, Any]:
        """
        Extracts cleaned title, year, season, and episode from raw media filename.
        e.g. 'Inception.2010.1080p.BluRay.x264.mkv' -> { 'title': 'Inception', 'year': 2010, 'is_series': False }
        e.g. 'Stranger.Things.S04E07.1080p.WEB-DL.mkv' -> { 'title': 'Stranger Things', 'season': 4, 'episode': 7, 'is_series': True }
        """
        clean = filename
        clean = os.path.splitext(clean)[0]
        clean = re.sub(r"[._]", " ", clean).strip()

        # Check for Series Season / Episode: S01E02, 1x02, Season 1 Episode 2
        series_match = re.search(r"(.*?)\s*(?:[sS](\d{1,2})[eE](\d{1,2})|(\d{1,2})[xX](\d{1,2})|[sS]eason\s*(\d{1,2})\s*[eE]pisode\s*(\d{1,2}))", clean, re.IGNORECASE)
        if series_match:
            raw_title = series_match.group(1).strip()
            s_num = int(series_match.group(2) or series_match.group(4) or series_match.group(6) or 1)
            e_num = int(series_match.group(3) or series_match.group(5) or series_match.group(7) or 1)
            
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
        clean_base = re.sub(r"[^\w\-]", "_", os.path.splitext(filename)[0])
        return SUBTITLES_DIR / f"{clean_base}.{lang}.srt"

    def get_cached_subtitle(self, filename: str, lang: str = "") -> Optional[Path]:
        """Instant (<0.1ms) check if subtitle is already cached on disk for requested or default languages."""
        target_langs = [l.strip().lower() for l in (lang or DEFAULT_SUBTITLE_LANG).split(",") if l.strip()]
        clean_base = re.sub(r"[^\w\-]", "_", os.path.splitext(filename)[0])
        raw_base = os.path.splitext(filename)[0]

        for l in target_langs:
            for b in (clean_base, raw_base):
                p = SUBTITLES_DIR / f"{b}.{l}.srt"
                if p.exists() and p.stat().st_size > 10:
                    return p

        # Check any cached subtitle for this base filename
        for b in (clean_base, raw_base):
            for f in SUBTITLES_DIR.glob(f"{b}.*.srt"):
                if f.is_file() and f.stat().st_size > 10:
                    return f
        return None

    def convert_srt_to_vtt(self, srt_content: str) -> str:
        """Converts SRT timestamp syntax to standard WebVTT format for browser video playback."""
        # Replace SRT comma decimals with WebVTT dot decimals: 00:00:01,000 -> 00:00:01.000
        vtt_body = re.sub(r"(\d{2}:\d{2}:\d{2}),(\d{3})", r"\1.\2", srt_content)
        return f"WEBVTT\n\n{vtt_body.strip()}\n"

    async def extract_embedded_subtitles(self, stream_url: str, filename: str) -> Dict[str, Path]:
        """
        Uses ffprobe to detect embedded subtitle tracks (SSA/ASS/SRT/VTT) and extracts them via ffmpeg.
        Returns a dictionary of language -> extracted subtitle Path.
        """
        extracted = {}
        clean_base = re.sub(r"[^\w\-]", "_", os.path.splitext(filename)[0])
        probe_cmd = [
            "ffprobe",
            "-v", "error",
            "-select_streams", "s",
            "-show_entries", "stream=index,codec_name:stream_tags=language,title",
            "-of", "json",
            stream_url
        ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *probe_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL
            )
            stdout_data, _ = await asyncio.wait_for(proc.communicate(), timeout=4.0)
            if not stdout_data:
                return extracted

            data = json.loads(stdout_data.decode("utf-8", errors="ignore"))
            streams = data.get("streams", [])
            for s_idx, s in enumerate(streams):
                lang = s.get("tags", {}).get("language", f"sub{s_idx}").lower().strip()
                out_path = SUBTITLES_DIR / f"{clean_base}.{lang}.srt"
                if out_path.exists() and out_path.stat().st_size > 10:
                    extracted[lang] = out_path
                    continue

                extract_cmd = [
                    "ffmpeg",
                    "-y",
                    "-hide_banner",
                    "-loglevel", "error",
                    "-vn", "-an",
                    "-i", stream_url,
                    "-map", f"0:s:{s_idx}",
                    "-c:s", "subrip",
                    str(out_path)
                ]
                try:
                    ext_proc = await asyncio.create_subprocess_exec(*extract_cmd)
                    await asyncio.wait_for(ext_proc.wait(), timeout=6.0)
                    if out_path.exists() and out_path.stat().st_size > 10:
                        extracted[lang] = out_path
                        logger.info(f"✅ Extracted embedded '{lang}' subtitle from stream: {out_path.name}")
                except Exception as ex_err:
                    logger.debug(f"Embedded subtitle extract error for stream {s_idx}: {ex_err}")
        except Exception as probe_err:
            logger.debug(f"Subtitle probe skipped for {filename}: {probe_err}")

        return extracted

    async def get_or_download_subtitle(self, filename: str, lang: str = "", stream_url: Optional[str] = None) -> Optional[Path]:
        """
        Dual-track hybrid subtitle pipeline:
        1. Checks instant local cache.
        2. Probes & extracts embedded container subtitle tracks if stream_url is provided.
        3. Queries OpenSubtitles mirror for missing subtitle languages.
        """
        cached = self.get_cached_subtitle(filename, lang)
        if cached:
            return cached

        # 1. Attempt embedded stream extraction only for local non-HTTP sources
        # Never run ffprobe over live MTProto proxy stream URLs as it steals connection bandwidth from VLC
        if stream_url and not (stream_url.startswith("http://") or stream_url.startswith("https://")):
            try:
                extracted = await self.extract_embedded_subtitles(stream_url, filename)
                if extracted:
                    target_langs = [l.strip().lower() for l in (lang or DEFAULT_SUBTITLE_LANG).split(",") if l.strip()]
                    for l in target_langs:
                        if l in extracted:
                            return extracted[l]
                    return next(iter(extracted.values()))
            except Exception as e:
                logger.debug(f"Embedded extraction notice for {filename}: {e}")

        # 2. OpenSubtitles API fallback
        meta = self.parse_media_title(filename)
        query = meta.get("query", filename)
        search_langs = [l.strip().lower() for l in (lang or DEFAULT_SUBTITLE_LANG).split(",") if l.strip()]
        logger.info(f"Searching subtitles for query: '{query}' ({search_langs})...")

        for l in search_langs:
            target_sub = self.get_cached_subtitle_path(filename, l)
            try:
                sub_path = await asyncio.to_thread(self._fetch_subtitle_sync, query, target_sub, l)
                if sub_path and sub_path.exists():
                    logger.info(f"✅ Auto-fetched '{l}' subtitle successfully: {sub_path.name}")
                    return sub_path
            except Exception as e:
                logger.debug(f"OpenSubtitles search notice for '{query}' ({l}): {e}")

        return None

    def _fetch_subtitle_sync(self, query: str, target_file: Path, lang: str = "eng") -> Optional[Path]:
        """Synchronously queries OpenSubtitles / Subtitle search endpoint."""
        clean_query = urllib.parse.quote(query)
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
                        best = data[0]
                        download_url = best.get("SubDownloadLink")
                        if download_url:
                            sub_req = urllib.request.Request(
                                download_url,
                                headers={"User-Agent": "TGPowerSuite v3.4.0"}
                            )
                            with urllib.request.urlopen(sub_req, timeout=8) as sub_res:
                                raw_bytes = sub_res.read()
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

    def trigger_prefetch(self, filename: str, stream_url: Optional[str] = None):
        """Non-blocking background prefetcher."""
        if filename in self._active_prefetches and not self._active_prefetches[filename].done():
            return
        task = asyncio.create_task(self.get_or_download_subtitle(filename, stream_url=stream_url))
        self._active_prefetches[filename] = task


# Global singleton subtitle service
subtitle_service = SubtitleService()
