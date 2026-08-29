import asyncio
import logging
import random
from typing import Any, Callable, Coroutine, TypeVar
from telethon.errors import FloodWaitError, RpcCallFailError

logger = logging.getLogger("account_shield")

T = TypeVar("T")

class TelegramAccountShield:
    """
    Automated Account Defense & Anti-Ban Shield for Personal Telegram Accounts.
    Protects against FloodWait bans, aggressive scraper detection, and concurrency overloads.
    """
    _semaphore = asyncio.Semaphore(4)  # Max 4 concurrent MTProto operations to avoid flagging
    _last_request_time = 0.0

    @classmethod
    async def safe_call(cls, coro_fn: Callable[..., Coroutine[Any, Any, T]], *args, context: str = "MTProto", **kwargs) -> T:
        """
        Executes a Telethon coroutine with rate limiting, micro-jitter, and FloodWait auto-cooldown.
        """
        async with cls._semaphore:
            # 1. Micro-jitter spacing (40ms - 90ms) to simulate human client behavior
            jitter = random.uniform(0.04, 0.09)
            await asyncio.sleep(jitter)

            for attempt in range(3):
                try:
                    return await coro_fn(*args, **kwargs)
                except FloodWaitError as e:
                    cooldown = e.seconds + 2
                    logger.warning(
                        f"🛡️ [Account Shield] FloodWait ({e.seconds}s) detected during {context}. "
                        f"Auto-cooldown active for {cooldown}s to protect personal Telegram account..."
                    )
                    await asyncio.sleep(cooldown)
                except RpcCallFailError as e:
                    logger.warning(f"🛡️ [Account Shield] RPC failure during {context}: {e}. Retrying in 2s...")
                    await asyncio.sleep(2)
                except Exception as e:
                    logger.error(f"🛡️ [Account Shield] Error in {context}: {e}")
                    raise

            # Final attempt
            return await coro_fn(*args, **kwargs)

account_shield = TelegramAccountShield()
