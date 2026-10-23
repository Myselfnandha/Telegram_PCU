"""
Adaptive FloodWait & Rate-Limit Governor for TG Power Suite.
Protects MTProto connections from rate limits, spreads burst chunks with micro-jitter,
and manages zero-loss automatic pause and resume during Telegram FloodWait cooldowns.
"""

import time
import random
import logging
import asyncio
from contextlib import asynccontextmanager
from typing import Optional, Dict, Any

logger = logging.getLogger("rate_governor")


class RateGovernor:
    """
    Adaptive FloodWait & Rate-Limit Governor for TG Power Suite.
    Uses high-speed non-blocking Token Bucket pacing with AIMD congestion control.
    Protects MTProto connections from rate limits, eliminates serial lock contention across
    parallel streams (FDM/IDM/browsers), and auto-tunes throughput to the maximum
    safe ceiling Telegram servers allow.
    """
    def __init__(
        self,
        initial_rate: float = 11.0,
        burst_capacity: float = 3.0,
        min_rate: float = 5.0,
        max_rate: float = 12.0,
    ):
        self._lock = asyncio.Lock()
        self._fallback_lock = asyncio.Lock()
        self._last_fallback_time: float = 0.0
        self._cooldown_until: float = 0.0
        self._cooldown_reason: str = ""
        self._cooldown_event = asyncio.Event()
        self._cooldown_event.set()  # Set means NOT cooling down (allowed to proceed)

        # Token Bucket parameters
        self._capacity: float = burst_capacity
        self._tokens: float = burst_capacity
        self._rate: float = initial_rate  # Tokens (1MB chunks) per second
        self._min_rate: float = min_rate
        self._max_rate: float = max_rate
        self._target_time: float = time.monotonic()

        self._total_flood_events: int = 0
        self._last_flood_time: float = 0.0
        self._last_ramp_time: float = time.monotonic()

    def configure_for_tier(self, is_premium: bool):
        """Auto-tunes token bucket and AIMD limits based on account tier to prevent Telegram FloodWaits."""
        if is_premium:
            self._rate = 24.0
            self._min_rate = 12.0
            self._max_rate = 32.0
            self._capacity = 6.0
            logger.info("⚡ RateGovernor: Configured for Telegram Premium tier (24-32 MB/s ceiling).")
        else:
            self._rate = 4.5
            self._min_rate = 1.8
            self._max_rate = 6.5
            self._capacity = 1.5
            logger.info("🛡️ RateGovernor: Configured for Standard tier (1.8-6.5 MB/s safe ceiling, eliminates recursive pauses).")

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

    @property
    def current_rate_limit_mbs(self) -> float:
        return self._rate

    def report_flood_wait(self, seconds: int, reason: str = "Telegram MTProto FloodWait", client=None):
        """
        Registers a FloodWait event. Immediately closes the gate,
        pausing all active workers cleanly until the cooldown expires.
        Applies Multiplicative Decrease (MD) to safely adapt to server limits.
        """
        now = time.time()
        cooldown_sec = max(2, int(seconds) + 1)  # 1s safety buffer
        until = now + cooldown_sec
        self._cooldown_until = max(self._cooldown_until, until)
        self._cooldown_reason = reason
        self._cooldown_event.clear()

        # Debounce: if multiple workers hit FloodWait during the same burst, avoid double backoff
        if (now - self._last_flood_time) < 1.5:
            return

        self._total_flood_events += 1
        self._last_flood_time = now

        # Multiplicative decrease: back off target dispatch rate by 20%
        old_rate = self._rate
        self._rate = max(self._min_rate, self._rate * 0.80)
        self._tokens = 0.0
        self._target_time = time.monotonic() + cooldown_sec

        # Clear Telethon's internal cached flood wait dict so Telethon doesn't do a secondary hidden freeze
        if client and hasattr(client, "_flood_waited_requests"):
            try:
                client._flood_waited_requests.clear()
            except Exception:
                pass

        logger.warning(
            f"⚠️ RateGovernor: Telegram FloodWait triggered ({seconds}s)! "
            f"Pausing transfers for {cooldown_sec}s. Backing off rate: {old_rate:.1f} -> {self._rate:.1f} MB/s. "
            f"Reason: {reason}"
        )

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._auto_release_gate(cooldown_sec))
        except RuntimeError:
            pass

    async def _auto_release_gate(self, delay: float):
        await asyncio.sleep(delay)
        if time.time() >= self._cooldown_until:
            self._cooldown_event.set()
            self._last_ramp_time = time.monotonic()
            logger.info("✅ RateGovernor: Cooldown expired. Gate reopened. Resuming active transfers seamlessly.")

    async def wait_if_cooling_down(self):
        """Workers call this before executing MTProto requests to pause cleanly if cooldown is active."""
        if not self.is_cooling_down:
            if not self._cooldown_event.is_set():
                self._cooldown_event.set()
            return
        if not self._cooldown_event.is_set():
            logger.debug(f"Worker waiting on RateGovernor gate ({self.cooldown_seconds_remaining}s remaining)...")
            await self._cooldown_event.wait()

    async def acquire_chunk_slot(self, chunk_size_bytes: int = 1024 * 1024):
        """
        Acquires permission to dispatch a chunk.
        Uses debt-based non-blocking token scheduling with micro-jitter to eliminate thundering herd.
        Clamps max debt delay to 0.8s to prevent accumulating multi-second pauses while enforcing pacing.
        """
        was_cooling = self.is_cooling_down
        await self.wait_if_cooling_down()
        if was_cooling:
            # Stagger workers upon gate re-opening to prevent simultaneous request spikes
            await asyncio.sleep(random.uniform(0.10, 0.35))

        delay = 0.0
        tokens_needed = chunk_size_bytes / (1024 * 1024)
        
        async with self._lock:
            now = time.monotonic()

            # Additive increase: if running cleanly for >20s, gently probe higher
            if (now - self._last_flood_time > 30.0) and (now - self._last_ramp_time > 20.0):
                if self._rate < self._max_rate:
                    self._rate = min(self._max_rate, self._rate + 0.5)
                    self._last_ramp_time = now

            if now > self._target_time:
                elapsed = now - self._target_time
                self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
                self._target_time = now

            if self._tokens >= tokens_needed:
                self._tokens -= tokens_needed
                delay = 0.0
            else:
                self._target_time = self._target_time + (tokens_needed / self._rate)
                raw_delay = self._target_time - now
                if raw_delay > 0.8:
                    self._target_time = now + 0.8
                    delay = 0.8
                else:
                    delay = max(0.0, raw_delay)

        if delay > 0:
            await asyncio.sleep(delay)

    @asynccontextmanager
    async def fallback_slot(self):
        """
        Coordinates sequential fallback requests during MTProto cooldown across all active streams/segments.
        Ensures only ONE MTProto request is in flight to Telegram at any time,
        paced with a 300ms interval (~3.3 MB/s safe baseline).
        Automatically releases so next segment can download without pausing download managers.
        """
        async with self._fallback_lock:
            now = time.monotonic()
            elapsed = now - self._last_fallback_time
            if elapsed < 0.30:
                await asyncio.sleep(0.30 - elapsed)
            try:
                yield
            finally:
                self._last_fallback_time = time.monotonic()

    def get_status(self) -> Dict[str, Any]:
        """Returns the current governor status for UI and monitoring."""
        cooling = self.is_cooling_down
        return {
            "is_cooling_down": cooling,
            "cooldown_seconds_remaining": self.cooldown_seconds_remaining if cooling else 0,
            "cooldown_until": self._cooldown_until if cooling else 0.0,
            "reason": self.reason,
            "current_rate_limit_mbs": round(self._rate, 1),
            "total_flood_events": self._total_flood_events,
            "last_flood_time": self._last_flood_time,
            "status_label": f"Fallback Normal Mode ({self.cooldown_seconds_remaining}s cooldown)" if cooling else f"Active & Protected ({self._rate:.0f} MB/s)"
        }


# Global singleton rate governor
rate_governor = RateGovernor()
