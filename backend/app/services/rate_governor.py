"""
Adaptive FloodWait & Rate-Limit Governor for TG Power Suite.
Protects MTProto connections from rate limits, spreads burst chunks with micro-jitter,
and manages zero-loss automatic pause and resume during Telegram FloodWait cooldowns.
"""

import time
import random
import logging
import asyncio
from typing import Optional, Dict, Any

logger = logging.getLogger("rate_governor")


class RateGovernor:
    def __init__(self):
        self._lock = asyncio.Lock()
        self._cooldown_until: float = 0.0
        self._cooldown_reason: str = ""
        self._active_multiplier: float = 1.0
        self._cooldown_event = asyncio.Event()
        self._cooldown_event.set()  # Set means NOT cooling down (allowed to proceed)
        self._total_flood_events: int = 0
        self._last_flood_time: float = 0.0

    @property
    def is_cooling_down(self) -> bool:
        return time.time() < self._cooldown_until

    @property
    def cooldown_seconds_remaining(self) -> int:
        rem = self._cooldown_until - time.time()
        return max(0, int(rem))

    @property
    def reason(self) -> str:
        return self._cooldown_reason if self.is_cooling_down else "Normal"

    def report_flood_wait(self, seconds: int, reason: str = "Telegram MTProto FloodWait"):
        """
        Registers a FloodWait event. Immediately closes the gate,
        pausing all active workers cleanly until the cooldown expires.
        """
        cooldown_sec = max(2, int(seconds) + 1)  # 1s safety buffer
        until = time.time() + cooldown_sec
        self._cooldown_until = max(self._cooldown_until, until)
        self._cooldown_reason = reason
        self._total_flood_events += 1
        self._last_flood_time = time.time()
        self._cooldown_event.clear()

        logger.warning(
            f"⚠️ RateGovernor: Telegram FloodWait triggered! Pausing all transfers for {cooldown_sec}s (until {time.strftime('%H:%M:%S', time.localtime(self._cooldown_until))}). Reason: {reason}"
        )

        # Schedule automatic gate release in background
        asyncio.create_task(self._auto_release_gate(cooldown_sec))

    async def _auto_release_gate(self, delay: float):
        await asyncio.sleep(delay)
        if time.time() >= self._cooldown_until:
            self._cooldown_event.set()
            logger.info("✅ RateGovernor: Cooldown expired. Gate reopened. Resuming active transfers seamlessly.")

    async def wait_if_cooling_down(self):
        """Workers call this before executing MTProto requests to pause cleanly if cooldown is active."""
        if not self._cooldown_event.is_set():
            logger.debug(f"Worker waiting on RateGovernor gate ({self.cooldown_seconds_remaining}s remaining)...")
            await self._cooldown_event.wait()

    async def acquire_chunk_slot(self, chunk_size_bytes: int = 512 * 1024):
        """
        Acquires a slot to send/receive a chunk.
        Applies dynamic micro-jitter (2ms - 15ms) to spread bursts across sub-connections.
        """
        await self.wait_if_cooling_down()
        # Add a tiny adaptive micro-jitter to prevent simultaneous burst collisions
        jitter_ms = random.uniform(0.002, 0.012)
        await asyncio.sleep(jitter_ms)

    def get_status(self) -> Dict[str, Any]:
        """Returns the current governor status for UI and monitoring."""
        cooling = self.is_cooling_down
        return {
            "is_cooling_down": cooling,
            "cooldown_seconds_remaining": self.cooldown_seconds_remaining if cooling else 0,
            "cooldown_until": self._cooldown_until if cooling else 0.0,
            "reason": self.reason,
            "total_flood_events": self._total_flood_events,
            "last_flood_time": self._last_flood_time,
            "status_label": f"Paused ({self.cooldown_seconds_remaining}s left)" if cooling else "Active & Protected"
        }


# Global singleton rate governor
rate_governor = RateGovernor()
