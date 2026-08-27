import asyncio
import logging
import time
from pathlib import Path
from typing import Optional, Callable, Any
from telethon import TelegramClient
from telethon.errors import (
    FloodWaitError,
    FilePartsInvalidError,
    RPCError,
    ChatAdminRequiredError,
    UserIsBlockedError
)
from app.services.file_detector import detect_mime, categorize_file
from app.services.thumbnail import generate_video_thumbnail
from app.services.fast_uploader import upload_file_turbo

logger = logging.getLogger("uploader")

class UploadCancelledError(Exception):
    pass

class UploadPausedError(Exception):
    pass

class UploadFailedError(Exception):
    pass


async def send_file_to_telegram(
    client: TelegramClient,
    file_path: Path,
    chat_id: Any,
    caption: str = "",
    custom_filename: Optional[str] = None,
    send_as: str = "auto",  # auto, document, media
    progress_callback: Optional[Callable[[int, int], None]] = None,
    pause_event: Optional[asyncio.Event] = None,
    cancel_event: Optional[asyncio.Event] = None,
    byte_offset: int = 0,
    byte_length: Optional[int] = None,
    max_retries: int = 3
) -> Any:
    """
    Sends a file (or zero-copy slice) to Telegram MTProto via Telethon.
    Handles auto-detection, thumbnail generation, streaming flags,
    FloodWaitError rate limits, and pause/cancellation hooks.
    """
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    filename_to_use = custom_filename or file_path.name
    mime = detect_mime(file_path, filename=filename_to_use)
    category = categorize_file(mime, filename=filename_to_use)

    # Determine force_document & streaming flags
    if send_as == "document":
        force_document = True
        supports_streaming = False
    elif send_as == "media":
        force_document = False
        supports_streaming = (category == "video")
    else:  # auto
        # Photos < 10MB can be sent as photo/media; large videos can stream; rest as document
        if category in ("video", "audio"):
            force_document = False
            supports_streaming = (category == "video")
        elif category == "photo" and file_path.stat().st_size <= 10 * 1024 * 1024:
            force_document = False
            supports_streaming = False
        else:
            force_document = True
            supports_streaming = False

    # Thumbnail generation for videos
    thumb_path: Optional[Path] = None
    if category == "video" and not force_document:
        try:
            thumb_path = await generate_video_thumbnail(file_path)
        except Exception as e:
            logger.warning(f"Failed to generate thumbnail: {e}")

    # Wrapped progress callback that also checks pause and cancellation
    def _safe_progress(current: int, total: int):
        if cancel_event and cancel_event.is_set():
            raise UploadCancelledError("Upload was cancelled by user.")
        
        if pause_event and not pause_event.is_set():
            raise UploadPausedError("Upload paused by user.")

        if progress_callback:
            try:
                progress_callback(current, total)
            except (UploadCancelledError, UploadPausedError):
                raise
            except Exception as e:
                logger.debug(f"Progress callback error: {e}")

    # Resolve target chat peer
    target_chat = chat_id
    if isinstance(chat_id, str):
        if chat_id.strip() == "me":
            target_chat = "me"
        elif chat_id.strip().lstrip("-").isdigit():
            target_chat = int(chat_id.strip())

    try:
        if target_chat == "me":
            entity = "me"
        else:
            try:
                entity = await client.get_input_entity(target_chat)
            except Exception:
                entity = await client.get_entity(target_chat)
    except Exception as e:
        logger.error(f"Could not resolve Telegram entity for '{target_chat}': {e}")
        raise UploadFailedError(f"Could not resolve destination chat '{target_chat}': {e}")

    # Retry loop for resilience
    attempt = 1
    while attempt <= max_retries:
        if cancel_event and cancel_event.is_set():
            raise UploadCancelledError("Upload cancelled before attempt.")

        # Check pause event
        if pause_event:
            await pause_event.wait()

        try:
            logger.info(
                f"Sending '{filename_to_use}' (slice offset={byte_offset}, len={byte_length}) to chat {target_chat} "
                f"[Attempt {attempt}/{max_retries}] [force_doc={force_document}, mime={mime}]"
            )

            # 1. Turbo multi-connection parallel upload chunks to Telegram servers (with zero-copy slice support)
            input_file = await upload_file_turbo(
                client=client,
                file_path=file_path,
                filename=filename_to_use,
                progress_callback=_safe_progress,
                pause_event=pause_event,
                cancel_event=cancel_event,
                byte_offset=byte_offset,
                byte_length=byte_length
            )

            # 2. Dispatch the Telegram message instantly with the uploaded file handle
            message = await client.send_file(
                entity=entity,
                file=input_file,
                caption=caption,
                force_document=force_document,
                supports_streaming=supports_streaming,
                thumb=thumb_path if thumb_path and thumb_path.exists() else None,
                file_name=filename_to_use
            )

            logger.info(f"Successfully uploaded {filename_to_use} (Message ID: {message.id})")
            return message

        except UploadPausedError:
            logger.info(f"Upload paused for {filename_to_use}. Awaiting resume signal...")
            if pause_event:
                await pause_event.wait()
            logger.info(f"Upload resumed for {filename_to_use}. Resuming upload stream...")
            # Do not increment attempt count when paused
            continue

        except FloodWaitError as e:
            wait_time = e.seconds + 2
            logger.warning(f"Telegram FloodWaitError: waiting {wait_time}s before retry...")
            if progress_callback:
                try:
                    progress_callback(0, file_path.stat().st_size)
                except Exception:
                    pass
            await asyncio.sleep(wait_time)
            attempt += 1

        except FilePartsInvalidError as e:
            logger.warning(f"FilePartsInvalidError on attempt {attempt}: {e}. Retrying in {attempt * 3}s...")
            if attempt >= max_retries:
                raise UploadFailedError(f"Upload failed after {max_retries} attempts: {e}") from e
            await asyncio.sleep(attempt * 3)
            attempt += 1

        except UploadCancelledError:
            logger.info(f"Upload cancelled by user: {filename_to_use}")
            raise

        except (ChatAdminRequiredError, UserIsBlockedError) as e:
            logger.error(f"Permission error sending to chat {target_chat}: {e}")
            raise UploadFailedError(f"Permission denied: {e}") from e

        except RPCError as e:
            logger.error(f"Telegram RPC error on attempt {attempt}: {e}")
            if attempt >= max_retries:
                raise UploadFailedError(f"Telegram RPC Error: {e.message if hasattr(e, 'message') else str(e)}") from e
            await asyncio.sleep(attempt * 2)
            attempt += 1

        except Exception as e:
            logger.error(f"Unexpected upload exception on attempt {attempt}: {e}", exc_info=True)
            if attempt >= max_retries:
                raise UploadFailedError(f"Upload failed: {str(e)}") from e
            await asyncio.sleep(attempt * 2)
            attempt += 1

        finally:
            # Clean up thumbnail if created
            if thumb_path and thumb_path.exists():
                thumb_path.unlink(missing_ok=True)
