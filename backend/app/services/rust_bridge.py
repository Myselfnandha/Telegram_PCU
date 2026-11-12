import os
import time
import base64
import logging
import asyncio
import subprocess
from typing import Optional, Union, Dict, Any, List
from pathlib import Path
import httpx

from app.config import ROOT_DIR, API_ID, SESSION_STRING, SESSION_STRING_2

logger = logging.getLogger("rust_bridge")

class RustBridge:
    def __init__(self, base_url: str = "http://127.0.0.1:8099"):
        self.base_url = base_url.rstrip("/")
        self._process: Optional[subprocess.Popen] = None
        self._client: Optional[httpx.AsyncClient] = None
        self._lock = asyncio.Lock()
        self._binary_path: Optional[Path] = None
        self._consecutive_failures: int = 0
        self._circuit_broken_until: float = 0.0

    def trip_circuit(self, cooldown_seconds: float = 30.0):
        self._circuit_broken_until = max(self._circuit_broken_until, time.time() + cooldown_seconds)

    def reset_circuit(self):
        self._circuit_broken_until = 0.0
        self._consecutive_failures = 0

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
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
                self._process = subprocess.Popen(
                    [str(path)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
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
            if SESSION_STRING and API_ID:
                ok1 = await self.register_session(SESSION_STRING, API_ID, account_idx=0)
                reg_any = reg_any or ok1
            if SESSION_STRING_2 and API_ID:
                ok2 = await self.register_session(SESSION_STRING_2, API_ID, account_idx=1)
                reg_any = reg_any or ok2
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

        try:
            client = self._get_client()
            req_timeout = 30.0
            resp = await client.post("/api/stream/chunk", json=payload, timeout=req_timeout)
            if resp.status_code == 200:
                self._consecutive_failures = 0
                return resp.content
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
        except Exception as e:
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
