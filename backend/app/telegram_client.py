import asyncio
import logging
from typing import Optional
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
    SESSION_FILE_PATH
)

logger = logging.getLogger("telegram_client")

class TelegramClientManager:
    _instance: Optional["TelegramClientManager"] = None
    _client: Optional[TelegramClient] = None
    _lock = asyncio.Lock()
    _is_ready = False

    @classmethod
    async def get_client(cls) -> TelegramClient:
        """
        Retrieves the singleton Telethon client instance.
        Ensures the client is started within the current running asyncio event loop.
        """
        if cls._client is None or not cls._client.is_connected():
            async with cls._lock:
                if cls._client is None:
                    if not API_ID or not API_HASH:
                        logger.warning("TG_API_ID or TG_API_HASH is not set. Telethon client cannot start.")
                        raise ValueError("TG_API_ID and TG_API_HASH must be configured in environment variables.")

                    logger.info(f"Initializing Telethon client with session: {SESSION_FILE_PATH}")
                    # Telethon client with absolute session file path
                    cls._client = TelegramClient(
                        str(SESSION_FILE_PATH),
                        API_ID,
                        API_HASH,
                        connection_retries=5,
                        retry_delay=2,
                        auto_reconnect=True
                    )

                if not cls._client.is_connected():
                    logger.info("Connecting Telethon client to Telegram MTProto...")
                    await cls._client.connect()

                cls._is_ready = await cls._client.is_user_authorized()
                if cls._is_ready:
                    me = await cls._client.get_me()
                    logger.info(f"Telegram client authorized as: {getattr(me, 'first_name', '')} (@{getattr(me, 'username', 'N/A')})")
                else:
                    logger.warning("Telethon client connected but NOT authorized yet. Run setup_auth.py to log in.")

        return cls._client

    @classmethod
    async def is_authorized(cls) -> bool:
        """Check if user session is authorized without raising."""
        try:
            if not API_ID or not API_HASH:
                return False
            client = await cls.get_client()
            return await client.is_user_authorized()
        except Exception as e:
            logger.error(f"Error checking authorization status: {e}")
            return False

    @classmethod
    async def get_me_info(cls) -> Optional[dict]:
        """Fetch current authenticated Telegram user info."""
        try:
            client = await cls.get_client()
            if not await client.is_user_authorized():
                return None
            me = await client.get_me()
            return {
                "id": me.id,
                "first_name": me.first_name,
                "last_name": me.last_name,
                "username": me.username,
                "phone": me.phone,
                "is_bot": me.bot
            }
        except Exception as e:
            logger.error(f"Error getting Telegram profile: {e}")
            return None

    @classmethod
    async def disconnect(cls):
        """Cleanly disconnect client on shutdown."""
        async with cls._lock:
            if cls._client is not None:
                logger.info("Disconnecting Telethon client...")
                try:
                    if cls._client.is_connected():
                        await cls._client.disconnect()
                except Exception as e:
                    logger.error(f"Error during client disconnect: {e}")
                finally:
                    cls._client = None
                    cls._is_ready = False
