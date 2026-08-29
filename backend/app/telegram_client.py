import asyncio
import logging
from typing import Optional, Dict, Any
from telethon import TelegramClient
from telethon.errors import (
    SessionPasswordNeededError,
    PhoneNumberInvalidError,
    PhoneCodeInvalidError,
    PhoneCodeExpiredError
)
from app.config import (
    API_ID,
    API_HASH,
    BOT_TOKEN,
    SESSION_DIR,
    SESSION_FILE_PATH
)

logger = logging.getLogger("telegram_client")

class TelegramClientManager:
    _instance: Optional["TelegramClientManager"] = None
    _client: Optional[TelegramClient] = None
    _bot_client: Optional[TelegramClient] = None
    _lock = asyncio.Lock()
    _is_ready = False
    _cached_me: Optional[dict] = None

    @classmethod
    async def get_client(cls) -> TelegramClient:
        """
        Retrieves the singleton user Telethon client instance.
        If no user session is authorized but BOT_TOKEN is present, falls back to bot client.
        """
        if cls._client is None or not cls._client.is_connected():
            async with cls._lock:
                if cls._client is None:
                    if not API_ID or not API_HASH:
                        logger.warning("TG_API_ID or TG_API_HASH is not set. Telethon client cannot start.")
                        raise ValueError("TG_API_ID and TG_API_HASH must be configured in environment variables.")

                    logger.info(f"Initializing Telethon user client with session: {SESSION_FILE_PATH}")
                    # Enforce strict Linux file permissions on session directory (0o700) and files (0o600)
                    try:
                        if SESSION_DIR.exists():
                            SESSION_DIR.chmod(0o700)
                        for sf in SESSION_DIR.glob("*.session*"):
                            sf.chmod(0o600)
                    except Exception as perm_err:
                        logger.debug(f"Could not apply session chmod: {perm_err}")

                    cls._client = TelegramClient(
                        str(SESSION_FILE_PATH),
                        API_ID,
                        API_HASH,
                        connection_retries=None,  # Infinite retries so network drops never permanently kill client
                        retry_delay=3,
                        auto_reconnect=True,
                        timeout=15
                    )

                if not cls._client.is_connected():
                    logger.info("Connecting Telethon user client to Telegram MTProto...")
                    try:
                        await cls._client.connect()
                    except Exception as conn_err:
                        logger.warning(f"Initial MTProto connect error (will retry in background): {conn_err}")

                cls._is_ready = await cls._client.is_user_authorized()
                if cls._is_ready:
                    me = await cls._client.get_me()
                    full_name = f"{getattr(me, 'first_name', '') or ''} {getattr(me, 'last_name', '') or ''}".strip()
                    cls._cached_me = {
                        "id": getattr(me, "id", None),
                        "name": full_name or getattr(me, "username", "Telegram User"),
                        "username": getattr(me, "username", None),
                        "is_premium": getattr(me, "premium", False)
                    }
                    logger.info(f"Telegram user client authorized as: {getattr(me, 'first_name', '')} (@{getattr(me, 'username', 'N/A')})")
                elif BOT_TOKEN:
                    logger.info("User session not authorized; attempting Bot login with BOT_TOKEN...")
                    try:
                        await cls._client.start(bot_token=BOT_TOKEN)
                        cls._is_ready = True
                        me = await cls._client.get_me()
                        cls._cached_me = {
                            "id": getattr(me, "id", None),
                            "name": getattr(me, "first_name", "Telegram Bot"),
                            "username": getattr(me, "username", None)
                        }
                        logger.info(f"Telegram client started as Bot: @{getattr(me, 'username', 'N/A')}")
                    except Exception as bot_err:
                        logger.warning(f"Failed bot login on user client: {bot_err}")
                else:
                    cls._cached_me = None
                    logger.warning("Telethon client connected but NOT authorized yet. Run setup_auth.py to log in.")

        return cls._client

    @classmethod
    async def get_bot_client(cls) -> Optional[TelegramClient]:
        """Returns dedicated bot client if BOT_TOKEN is set."""
        if not BOT_TOKEN or not API_ID or not API_HASH:
            return None

        if cls._bot_client is None or not cls._bot_client.is_connected():
            async with cls._lock:
                if cls._bot_client is None:
                    bot_session = SESSION_DIR / "bot_session"
                    cls._bot_client = TelegramClient(
                        str(bot_session),
                        API_ID,
                        API_HASH,
                        connection_retries=10,
                        retry_delay=2,
                        auto_reconnect=True
                    )
                if not cls._bot_client.is_connected():
                    await cls._bot_client.start(bot_token=BOT_TOKEN)
                    me = await cls._bot_client.get_me()
                    logger.info(f"Dedicated Bot client connected as @{getattr(me, 'username', '')}")

        return cls._bot_client

    @classmethod
    async def is_authorized(cls) -> bool:
        """Check if client is authorized (instant memory check first)."""
        try:
            if cls._client is not None and cls._client.is_connected() and cls._cached_me:
                return True
            if not SESSION_FILE_PATH.exists():
                return False
            client = await cls.get_client()
            return await client.is_user_authorized()
        except Exception as e:
            logger.debug(f"Error checking authorization status: {e}")
            return False

    @classmethod
    async def get_me_info(cls) -> Optional[dict]:
        """Fetch current authenticated Telegram user info (served from memory cache with phone privacy masking)."""
        if cls._cached_me and "phone" in cls._cached_me:
            return cls._cached_me
        try:
            client = await cls.get_client()
            if not await client.is_user_authorized():
                return None
            me = await client.get_me()
            full_name = f"{getattr(me, 'first_name', '') or ''} {getattr(me, 'last_name', '') or ''}".strip()
            raw_phone = getattr(me, "phone", None)
            masked_phone = None
            if raw_phone:
                raw_str = str(raw_phone)
                masked_phone = f"+{raw_str[:3]}******{raw_str[-4:]}" if len(raw_str) >= 7 else "***"

            cls._cached_me = {
                "id": me.id,
                "first_name": getattr(me, "first_name", ""),
                "last_name": getattr(me, "last_name", ""),
                "name": full_name or getattr(me, "username", "Telegram User"),
                "username": getattr(me, "username", None),
                "phone": masked_phone,
                "is_bot": getattr(me, "bot", False)
            }
            return cls._cached_me
        except Exception as e:
            logger.error(f"Error getting Telegram profile: {e}")
            return None

    @classmethod
    async def disconnect(cls):
        """Cleanly disconnect all clients on shutdown."""
        async with cls._lock:
            if cls._client is not None:
                logger.info("Disconnecting Telethon user client...")
                try:
                    if cls._client.is_connected():
                        await cls._client.disconnect()
                except Exception as e:
                    logger.error(f"Error during user client disconnect: {e}")
                finally:
                    cls._client = None
                    cls._is_ready = False

            if cls._bot_client is not None:
                logger.info("Disconnecting Telethon bot client...")
                try:
                    if cls._bot_client.is_connected():
                        await cls._bot_client.disconnect()
                except Exception as e:
                    logger.error(f"Error during bot client disconnect: {e}")
                finally:
                    cls._bot_client = None
