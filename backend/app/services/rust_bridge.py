import os
import time
import base64
import logging
import asyncio
import subprocess
import secrets
from typing import Optional, Union, Dict, Any, List
from pathlib import Path
import httpx

from app.config import ROOT_DIR, API_ID, SESSION_STRING, SESSION_STRING_2

logger = logging.getLogger("rust_bridge")

def _get_or_create_auth_token() -> str:
    env_token = os.getenv("TG_RUST_CORE_AUTH_TOKEN", "").strip()
    if env_token:
        return env_token
    token_file = ROOT_DIR / "backend" / "data" / ".rust_core_token"
    try:
        if token_file.exists():
            content = token_file.read_text().strip()
            if content:
                os.environ["TG_RUST_CORE_AUTH_TOKEN"] = content
                return content
    except Exception:
        pass
    new_token = secrets.token_hex(32)
    try:
        token_file.parent.mkdir(parents=True, exist_ok=True)
        token_file.write_text(new_token)
        os.chmod(token_file, 0o600)
    except Exception:
        pass
    os.environ["TG_RUST_CORE_AUTH_TOKEN"] = new_token
    return new_token

_RUST_CORE_AUTH_TOKEN = _get_or_create_auth_token()

class RustBridge:
    def __init__(self, base_url: str = "http://127.0.0.1:8099"):
        self.base_url = base_url.rstrip("/")
        self._process: Optional[subprocess.Popen] = None
        self._client: Optional[httpx.AsyncClient] = None
        self._lock = asyncio.Lock()
        self._binary_path: Optional[Path] = None
        self._consecutive_failures: int = 0
        self._circuit_broken_until: float = 0.0
        self._log_file = None
        self.chunks_requested: int = 0
        self.chunks_completed: int = 0
        self.chunks_cancelled: int = 0
        self.chunks_timed_out: int = 0
        self.chunks_failed: int = 0
        self.bytes_delivered: int = 0
        self.python_fallback_chunks: int = 0
        self.last_latency_ms: float = 0.0
        self.total_latency_ms: float = 0.0

    @property
    def chunks_served(self) -> int:
        return self.chunks_completed

    @chunks_served.setter
    def chunks_served(self, val: int):
        self.chunks_completed = val

    def get_telemetry(self) -> Dict[str, Any]:
        avg_lat = (self.total_latency_ms / self.chunks_completed) if self.chunks_completed > 0 else 0.0
        return {
            "chunks_requested": self.chunks_requested,
            "chunks_completed": self.chunks_completed,
            "chunks_served": self.chunks_completed,
            "chunks_cancelled": self.chunks_cancelled,
            "chunks_timed_out": self.chunks_timed_out,
            "chunks_failed": self.chunks_failed,
            "bytes_delivered": self.bytes_delivered,
            "bytes_delivered_mb": round(self.bytes_delivered / (1024 * 1024), 2),
            "python_fallback_chunks": self.python_fallback_chunks,
            "avg_latency_ms": round(avg_lat, 1),
            "last_latency_ms": round(self.last_latency_ms, 1),
        }

    def trip_circuit(self, cooldown_seconds: float = 30.0):
        self._circuit_broken_until = max(self._circuit_broken_until, time.time() + cooldown_seconds)

    def reset_circuit(self):
        self._circuit_broken_until = 0.0
        self._consecutive_failures = 0

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            headers = {"Authorization": f"Bearer {_RUST_CORE_AUTH_TOKEN}"}
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=headers,
                timeout=httpx.Timeout(45.0, connect=5.0),
                limits=httpx.Limits(max_keepalive_connections=64, max_connections=128)
            )
        return self._client

    def find_binary(self) -> Optional[Path]:
        candidates = [
            ROOT_DIR / "rust_core" / "target" / "release" / "tg-rust-core",
            ROOT_DIR / "rust_core" / "target" / "debug" / "tg-rust-core",
            Path("/usr/local/bin/tg-rust-core")
        ]
        for c in candidates:
            if c.exists() and os.access(c, os.X_OK):
                return c
        return None

    async def is_healthy(self) -> bool:
        now = time.time()
        if now < getattr(self, "_circuit_broken_until", 0.0):
            return False
        if (now - getattr(self, "_last_health_time", 0.0)) < 2.0:
            return getattr(self, "_cached_healthy", False)
        try:
            client = self._get_client()
            resp = await client.get("/health", timeout=2.0)
            if resp.status_code == 200:
                data = resp.json()
                active = data.get("active_accounts", 0)
                ok = data.get("status") == "ok" and active > 0
                if data.get("status") == "ok" and active == 0:
                    asyncio.create_task(self.ensure_sessions_registered())
                self._cached_healthy = ok
                self._last_health_time = now
                return ok
        except Exception:
            self._cached_healthy = False
            self._last_health_time = now
            return False
        self._cached_healthy = False
        self._last_health_time = now
        return False

    async def get_status(self) -> Dict[str, Any]:
        try:
            client = self._get_client()
            resp = await client.get("/health", timeout=2.0)
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            return {"status": "error", "error": str(e), "engine": "fallback"}
        return {"status": "unreachable", "engine": "fallback"}

    async def start_daemon(self, binary_path: Optional[str] = None) -> bool:
        async with self._lock:
            if await self.is_healthy():
                await self.ensure_sessions_registered()
                return True

            path = Path(binary_path) if binary_path else self.find_binary()
            if not path or not path.exists():
                logger.warning(f"tg-rust-core binary not found at {path}")
                return False

            self._binary_path = path
            try:
                env = os.environ.copy()
                env["TG_RUST_CORE_AUTH_TOKEN"] = _RUST_CORE_AUTH_TOKEN
                log_path = ROOT_DIR / "backend" / "data" / "rust_core.log"
                log_path.parent.mkdir(parents=True, exist_ok=True)
                if self._log_file and not self._log_file.closed:
                    try:
                        self._log_file.close()
                    except Exception:
                        pass
                self._log_file = open(log_path, "a")

                self._process = subprocess.Popen(
                    [str(path)],
                    env=env,
                    stdout=self._log_file,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    close_fds=True
                )
            except Exception as e:
                logger.error(f"Failed to spawn tg-rust-core: {e}")
                return False

            for _ in range(15):
                await asyncio.sleep(0.3)
                if await self.is_healthy():
                    logger.info("tg-rust-core daemon online on port 8099")
                    await self.ensure_sessions_registered()
                    return True

            return False

    async def stop_daemon(self):
        async with self._lock:
            if self._client and not self._client.is_closed:
                await self._client.aclose()
                self._client = None

            if self._process:
                try:
                    self._process.terminate()
                    await asyncio.to_thread(self._process.wait, 3)
                except Exception:
                    try:
                        self._process.kill()
                        await asyncio.to_thread(self._process.wait, 2)
                    except Exception:
                        pass
                self._process = None

    async def register_session(self, session_string: str, api_id: int, account_idx: Optional[int] = None) -> bool:
        if not session_string or not api_id:
            return False
        try:
            client = self._get_client()
            payload = {"session_string": session_string, "api_id": api_id}
            if account_idx is not None:
                payload["account_idx"] = account_idx
            resp = await client.post(
                "/api/session/register",
                json=payload,
                timeout=10.0
            )
            if resp.status_code == 200:
                return resp.json().get("success", False)
        except Exception as e:
            logger.debug(f"register_session error: {e}")
        return False

    async def ensure_sessions_registered(self) -> bool:
        if getattr(self, "_registering_sessions", False):
            return False
        self._registering_sessions = True
        try:
            reg_any = False
            s1 = (
                os.getenv("TG_SESSION_STRING", "").strip()
                or os.getenv("SESSION_STRING", "").strip()
                or SESSION_STRING
            )
            if not s1:
                try:
                    from app.telegram_client import TelegramClientManager
                    if await TelegramClientManager.is_authorized():
                        tg_client = await TelegramClientManager.get_client()
                        if tg_client and tg_client.is_connected() and hasattr(tg_client, "session"):
                            from telethon.sessions import StringSession
                            s1 = StringSession.save(tg_client.session)
                except Exception as e:
                    logger.debug(f"Could not extract StringSession from primary Telethon client: {e}")

            if s1 and API_ID:
                ok1 = await self.register_session(s1, API_ID, account_idx=0)
                reg_any = reg_any or ok1
                if ok1:
                    logger.info("Successfully registered primary Telegram session with Rust core")

            s2 = (
                os.getenv("TG_SESSION_STRING_2", "").strip()
                or os.getenv("SESSION_STRING_2", "").strip()
                or SESSION_STRING_2
            )
            if not s2:
                try:
                    from app.telegram_client import TelegramClientManager
                    if await TelegramClientManager.is_secondary_authorized():
                        tg_client2 = await TelegramClientManager.get_secondary_client()
                        if tg_client2 and tg_client2.is_connected() and hasattr(tg_client2, "session"):
                            from telethon.sessions import StringSession
                            s2 = StringSession.save(tg_client2.session)
                except Exception as e:
                    logger.debug(f"Could not extract StringSession from secondary Telethon client: {e}")

            if s2 and API_ID:
                ok2 = await self.register_session(s2, API_ID, account_idx=1)
                reg_any = reg_any or ok2
                if ok2:
                    logger.info("Successfully registered secondary Telegram session with Rust core")

            return reg_any
        finally:
            self._registering_sessions = False

    async def stream_chunk(
        self,
        dc_id: int,
        doc_id: int,
        access_hash: int,
        file_reference: Union[bytes, str],
        offset: int,
        limit: int,
        priority: str = "high",
        account_idx: Optional[int] = None
    ) -> Optional[bytes]:
        now = time.time()
        if now < getattr(self, "_circuit_broken_until", 0.0):
            return None

        if isinstance(file_reference, (bytes, bytearray)):
            file_ref_b64 = base64.b64encode(file_reference).decode("ascii")
        else:
            file_ref_b64 = str(file_reference)

        payload = {
            "dc_id": dc_id,
            "doc_id": doc_id,
            "access_hash": access_hash,
            "file_reference_b64": file_ref_b64,
            "offset": offset,
            "limit": limit,
            "priority": priority
        }
        if account_idx is not None:
            payload["account_idx"] = account_idx

        self.chunks_requested += 1
        t0 = time.time()
        try:
            client = self._get_client()
            req_timeout = 30.0
            resp = await client.post("/api/stream/chunk", json=payload, timeout=req_timeout)
            if resp.status_code == 200:
                self._consecutive_failures = 0
                data = resp.content
                duration_ms = (time.time() - t0) * 1000.0
                self.chunks_completed += 1
                self.bytes_delivered += len(data)
                self.last_latency_ms = duration_ms
                self.total_latency_ms += duration_ms
                return data
            self.chunks_failed += 1
            err_text = resp.text
            if "FLOOD" in err_text or resp.status_code == 429:
                import re
                m = re.search(r"Some\((\d+)\)", err_text) or re.search(r"FLOOD.*?(\d+)", err_text)
                secs = int(m.group(1)) if m else 3
                logger.info(f"Rust stream_chunk flood wait {secs}s on account {account_idx}")
                self.trip_circuit(float(secs))
                return None
            if "FILE_REFERENCE_EXPIRED" in err_text:
                raise RuntimeError("FILE_REFERENCE_EXPIRED")
            now = time.monotonic()
            if resp.status_code in (500, 502, 503, 504):
                self._consecutive_failures = getattr(self, "_consecutive_failures", 0) + 1
                if self._consecutive_failures >= 3:
                    if now >= getattr(self, "_circuit_broken_until", 0.0):
                        self.trip_circuit(3.0)
                        logger.warning(f"Rust bridge circuit tripped for 3s due to {self._consecutive_failures} consecutive errors ({resp.status_code})")
                        asyncio.create_task(self.ensure_sessions_registered())
            logger.warning(f"Rust stream_chunk failed: {resp.status_code} {err_text}")
        except asyncio.CancelledError:
            self.chunks_cancelled += 1
            raise
        except (httpx.TimeoutException, asyncio.TimeoutError):
            self.chunks_timed_out += 1
            now = time.monotonic()
            self._consecutive_failures = getattr(self, "_consecutive_failures", 0) + 1
            if self._consecutive_failures >= 3 and now >= getattr(self, "_circuit_broken_until", 0.0):
                self.trip_circuit(3.0)
            logger.warning("Rust stream_chunk timeout")
        except Exception as e:
            self.chunks_failed += 1
            if "FILE_REFERENCE_EXPIRED" in str(e):
                raise
            now = time.monotonic()
            self._consecutive_failures = getattr(self, "_consecutive_failures", 0) + 1
            if self._consecutive_failures >= 3:
                if now >= getattr(self, "_circuit_broken_until", 0.0):
                    self.trip_circuit(3.0)
                    logger.warning(f"Rust bridge circuit tripped for 3s due to {self._consecutive_failures} consecutive failures ({type(e).__name__})")
                    asyncio.create_task(self.ensure_sessions_registered())
            logger.warning(f"Rust stream_chunk error: {type(e).__name__} ({e or 'timeout'})")
        return None

    async def prewarm(
        self,
        dc_id: int,
        doc_id: int,
        access_hash: int,
        file_reference: Union[bytes, str],
        file_size: int
    ) -> bool:
        if isinstance(file_reference, (bytes, bytearray)):
            file_ref_b64 = base64.b64encode(file_reference).decode("ascii")
        else:
            file_ref_b64 = str(file_reference)

        payload = {
            "dc_id": dc_id,
            "doc_id": doc_id,
            "access_hash": access_hash,
            "file_reference_b64": file_ref_b64,
            "file_size": file_size
        }

        try:
            client = self._get_client()
            resp = await client.post("/api/stream/prewarm", json=payload, timeout=5.0)
            return resp.status_code in (200, 202)
        except Exception as e:
            logger.debug(f"prewarm error: {e}")
            return False

    async def upload_part(
        self,
        file_id: int,
        file_part: int,
        file_total_parts: int,
        chunk: bytes,
        account_idx: Optional[int] = None
    ) -> bool:
        payload = {
            "file_id": file_id,
            "file_part": file_part,
            "file_total_parts": file_total_parts,
            "bytes_b64": base64.b64encode(chunk).decode("ascii")
        }
        if account_idx is not None:
            payload["account_idx"] = account_idx

        try:
            client = self._get_client()
            resp = await client.post("/api/upload/part", json=payload, timeout=30.0)
            return resp.status_code == 200
        except Exception as e:
            logger.debug(f"upload_part error: {e}")
            return False

    async def report_flood(self, account_idx: int, seconds: int) -> bool:
        try:
            client = self._get_client()
            resp = await client.post(
                "/api/safety/report_flood",
                json={"account_idx": account_idx, "seconds": seconds},
                timeout=3.0
            )
            return resp.status_code == 200
        except Exception:
            return False

rust_bridge = RustBridge()
