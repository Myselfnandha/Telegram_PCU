import mimetypes
from pathlib import Path
from typing import Union

try:
    import magic
    HAS_MAGIC = True
except ImportError:
    HAS_MAGIC = False

def detect_mime(file_path_or_bytes: Union[str, Path, bytes], filename: str = "") -> str:
    """
    Detects MIME type accurately using magic bytes if available,
    with fallback to filename extension.
    """
    # 1. Try python-magic
    if HAS_MAGIC:
        try:
            if isinstance(file_path_or_bytes, (str, Path)):
                p = Path(file_path_or_bytes)
                if p.exists() and p.is_file() and p.stat().st_size > 0:
                    return magic.from_file(str(p), mime=True)
            elif isinstance(file_path_or_bytes, (bytes, bytearray)):
                if len(file_path_or_bytes) > 0:
                    return magic.from_buffer(file_path_or_bytes[:4096], mime=True)
        except Exception:
            pass

    # 2. Fallback to mimetypes guess
    name_to_check = filename
    if not name_to_check and isinstance(file_path_or_bytes, (str, Path)):
        name_to_check = str(file_path_or_bytes)

    if name_to_check:
        guessed, _ = mimetypes.guess_type(name_to_check)
        if guessed:
            return guessed

    return "application/octet-stream"


def categorize_file(mime: str, filename: str = "") -> str:
    """
    Categorizes the file into: 'photo', 'video', 'audio', 'document', 'archive', 'code'
    """
    mime = mime.lower()
    ext = Path(filename).suffix.lower() if filename else ""

    # Photos
    if mime in ("image/jpeg", "image/png", "image/webp") or ext in (".jpg", ".jpeg", ".png", ".webp"):
        return "photo"
    elif mime.startswith("image/"):
        return "image"

    # Videos
    if mime.startswith("video/") or ext in (".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv", ".wmv", ".m4v", ".ts"):
        return "video"

    # Audios
    if mime.startswith("audio/") or ext in (".mp3", ".flac", ".wav", ".aac", ".ogg", ".m4a", ".opus", ".wma"):
        return "audio"

    # Archives
    if mime in ("application/zip", "application/x-rar-compressed", "application/x-7z-compressed", "application/x-tar", "application/gzip") or \
       ext in (".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".iso"):
        return "archive"

    # Code / Text
    if mime.startswith("text/") or ext in (".py", ".js", ".html", ".css", ".json", ".xml", ".ts", ".c", ".cpp", ".rs", ".go", ".md", ".txt", ".sh"):
        return "code"

    return "document"
