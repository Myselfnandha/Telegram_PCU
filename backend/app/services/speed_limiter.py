import asyncio
import time
import logging
from typing import Optional

logger = logging.getLogger("speed_limiter")


class SpeedLimiter:
    """
    High-precision Asynchronous Token Bucket Rate Limiter.
    Dynamically throttles concurrent MTProto uploads to a target rate (in MB/s).
    A speed limit of 0 or None indicates unthrottled maximum network speed (Gigabit / Fiber).
    """

    def __init__(self, limit_mb_s: float = 0.0):
        self._limit_bytes_per_sec = float(limit_mb_s) * 1024 * 1024
        self._tokens = float(self._limit_bytes_per_sec) if self._limit_bytes_per_sec > 0 else float("inf")
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    @property
    def limit_mb_s(self) -> float:
        if self._limit_bytes_per_sec <= 0:
            return 0.0
        return round(self._limit_bytes_per_sec / (1024 * 1024), 2)

    def set_limit(self, limit_mb_s: float):
        """Dynamically adjusts the speed cap in real-time."""
        val = max(0.0, float(limit_mb_s))
        if val <= 0:
            self._limit_bytes_per_sec = 0.0
            self._tokens = float("inf")
            logger.info("Bandwidth Limiter disabled (Unlimited Gigabit/Fiber Mode).")
        else:
            self._limit_bytes_per_sec = val * 1024 * 1024
            # Cap initial bucket to prevent burst surges
            if self._tokens == float("inf"):
                self._tokens = 0.0
            else:
                self._tokens = min(self._tokens, self._limit_bytes_per_sec)
            self._last_refill = time.monotonic()
            logger.info(f"Bandwidth Limiter dynamically updated to {val:.1f} MB/s.")

    async def acquire(self, num_bytes: int):
        """
        Asynchronously acquires token allowance for transmitting `num_bytes`.
        If bucket has insufficient tokens, sleeps non-blockingly until tokens refill.
        """
        if self._limit_bytes_per_sec <= 0 or num_bytes <= 0:
            return

        while True:
            async with self._lock:
                now = time.monotonic()
                elapsed = now - self._last_refill
                self._last_refill = now

                # Add accumulated tokens based on elapsed time
                if elapsed > 0:
                    self._tokens = min(
                        self._limit_bytes_per_sec,
                        self._tokens + (elapsed * self._limit_bytes_per_sec)
                    )

                if self._tokens >= num_bytes:
                    self._tokens -= num_bytes
                    return
                else:
                    # Calculate required sleep duration to accumulate the missing tokens
                    missing = num_bytes - self._tokens
                    sleep_time = max(0.005, missing / self._limit_bytes_per_sec)

            await asyncio.sleep(min(1.0, sleep_time))


# Global singleton speed limiter
speed_limiter = SpeedLimiter(limit_mb_s=0.0)
