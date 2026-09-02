"""
Stream-to-Disk Offline Library Cacher & Organizer.
Automatically saves and organizes streamed media into the local video library.
"""

import os
import shutil
import logging
from pathlib import Path
from typing import Optional, Dict, Any

from app.services.subtitle_service import subtitle_service
from app.services.stream_cache import stream_cache_service, BLOCK_SIZE

logger = logging.getLogger("stream_saver")

DEFAULT_LIBRARY_DIR = Path.home() / "Videos" / "Telegram_Cinema"


class StreamSaverService:
    def __init__(self):
        self._enabled = False
        self._library_dir = DEFAULT_LIBRARY_DIR
        self._init_dirs()

    def _init_dirs(self):
        try:
            (self._library_dir / "Movies").mkdir(parents=True, exist_ok=True)
            (self._library_dir / "TV_Shows").mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.debug(f"Could not create library dirs: {e}")

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool):
        self._enabled = bool(enabled)
        logger.info(f"Offline Stream-to-Disk Cacher: {'Enabled' if self._enabled else 'Disabled'}")

    def get_organized_path(self, filename: str) -> Path:
        """
        Determines the target organized path in ~/Videos/Telegram_Cinema.
        Movies -> ~/Videos/Telegram_Cinema/Movies/Title (Year).ext
        TV Shows -> ~/Videos/Telegram_Cinema/TV_Shows/Series/Season XX/filename.ext
        """
        parsed = subtitle_service.parse_media_title(filename)
        ext = os.path.splitext(filename)[1] or ".mp4"
        clean_fn = "".join([c for c in filename if (c.isalnum() or c in " .-_()")]).strip()

        if parsed.get("is_series"):
            series_name = parsed.get("title", "TV_Series")
            series_clean = "".join([c for c in series_name if (c.isalnum() or c in " .-_()")]).strip()
            s_num = parsed.get("season", 1)
            season_folder = f"Season {s_num:02d}"
            target_dir = self._library_dir / "TV_Shows" / series_clean / season_folder
            target_dir.mkdir(parents=True, exist_ok=True)
            return target_dir / clean_fn
        else:
            title = parsed.get("title", "Movie")
            year = parsed.get("year")
            title_clean = "".join([c for c in title if (c.isalnum() or c in " .-_()")]).strip()
            movie_folder = f"{title_clean} ({year})" if year else title_clean
            target_dir = self._library_dir / "Movies" / movie_folder
            target_dir.mkdir(parents=True, exist_ok=True)
            return target_dir / clean_fn

    def save_completed_stream(self, chat_id: str, message_id: int, total_file_size: int, filename: str) -> Optional[Path]:
        """
        Checks if stream is complete and saves an organized copy into the local library.
        """
        if not self._enabled:
            return None

        total_blocks = (total_file_size + BLOCK_SIZE - 1) // BLOCK_SIZE
        # Check if all blocks exist on disk
        for i in range(total_blocks):
            expected = min(BLOCK_SIZE, total_file_size - (i * BLOCK_SIZE))
            if not stream_cache_service.has_block(str(chat_id), message_id, i, expected):
                return None

        target_file = self.get_organized_path(filename)
        if target_file.exists() and target_file.stat().st_size == total_file_size:
            return target_file

        tmp_target = target_file.with_suffix(".saving")
        try:
            with open(tmp_target, "wb") as out_f:
                for i in range(total_blocks):
                    block_file = stream_cache_service.get_block_path(str(chat_id), message_id, i)
                    with open(block_file, "rb") as bf:
                        shutil.copyfileobj(bf, out_f, length=1024 * 1024)
            tmp_target.rename(target_file)
            logger.info(f"🎬 Saved offline copy to library: {target_file}")
            return target_file
        except Exception as e:
            logger.warning(f"Could not save offline stream to library: {e}")
            if tmp_target.exists():
                tmp_target.unlink(missing_ok=True)
        return None


# Global singleton stream saver
stream_saver_service = StreamSaverService()
