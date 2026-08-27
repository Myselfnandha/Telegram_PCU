import asyncio
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Optional
from PIL import Image

logger = logging.getLogger("thumbnail")

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None

async def generate_video_thumbnail(video_path: Path) -> Optional[Path]:
    """
    Extracts a representative frame from a video file using FFmpeg,
    resizes it to max 320x320, compresses to JPEG <= 200KB for Telegram compatibility.
    Runs asynchronously in thread pool to prevent blocking the event loop.
    """
    if not FFMPEG_AVAILABLE:
        logger.debug("FFmpeg not found on system. Skipping video thumbnail generation.")
        return None

    if not video_path.exists() or video_path.stat().st_size == 0:
        return None

    thumb_path = video_path.parent / f"{video_path.stem}_thumb.jpg"

    def _extract_and_scale():
        try:
            # 1. Run FFmpeg to capture 1 frame at 1-second mark
            cmd = [
                "ffmpeg",
                "-y",  # overwrite
                "-ss", "00:00:01",
                "-i", str(video_path),
                "-vframes", "1",
                "-vf", "scale='min(320,iw)':-2",  # scale down to max width 320 maintaining aspect ratio
                "-q:v", "4",
                str(thumb_path)
            ]
            
            subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=25,
                check=True
            )

            if not thumb_path.exists() or thumb_path.stat().st_size == 0:
                return None

            # 2. Resize and verify dimensions & size with PIL
            with Image.open(thumb_path) as img:
                img = img.convert("RGB")
                img.thumbnail((320, 320), Image.Resampling.LANCZOS)
                
                # Save with controlled JPEG quality
                quality = 85
                img.save(thumb_path, "JPEG", quality=quality, optimize=True)

                # Ensure < 200 KB
                while thumb_path.stat().st_size > 200 * 1024 and quality > 30:
                    quality -= 15
                    img.save(thumb_path, "JPEG", quality=quality, optimize=True)

            return thumb_path

        except subprocess.TimeoutExpired:
            logger.warning(f"FFmpeg timed out extracting thumbnail for {video_path.name}")
            if thumb_path.exists():
                thumb_path.unlink(missing_ok=True)
            return None
        except Exception as e:
            logger.warning(f"Could not generate thumbnail for {video_path.name}: {e}")
            if thumb_path.exists():
                thumb_path.unlink(missing_ok=True)
            return None

    return await asyncio.to_thread(_extract_and_scale)
