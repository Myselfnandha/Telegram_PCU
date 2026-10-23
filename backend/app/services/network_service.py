# -*- coding: utf-8 -*-
"""
Network & Remote Streaming Service (Deep Module)
Encapsulates LAN IP resolution, dynamic QR code generation, and 
zero-configuration Cloudflare Quick Tunnel management for mobile VLC and cellular streaming.
"""

import os
import re
import io
import socket
import logging
import asyncio
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional, List
import qrcode
import psutil

logger = logging.getLogger("network_service")

BIN_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "bin"
CLOUDFLARED_BIN = BIN_DIR / "cloudflared"


class NetworkService:
    """
    Deep Module for Local Area Network Discovery and Cloudflare Tunneling.
    Provides clean endpoints for mobile connection, IP discovery, QR generation,
    and 1-click remote HTTPS tunneling.
    """

    def __init__(self):
        self._tunnel_proc: Optional[asyncio.subprocess.Process] = None
        self._tunnel_url: Optional[str] = None
        self._tunnel_error: Optional[str] = None
        self._tunnel_task: Optional[asyncio.Task] = None

    def get_local_ip(self) -> str:
        """Finds primary local IPv4 address connected to default gateway / Wi-Fi."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("1.1.1.1", 80))
                return s.getsockname()[0]
        except Exception:
            try:
                for iface, addrs in psutil.net_if_addrs().items():
                    for addr in addrs:
                        if addr.family == socket.AF_INET and not addr.address.startswith("127."):
                            return addr.address
            except Exception:
                pass
        return "127.0.0.1"

    def get_network_info(self, port: int = 8088) -> Dict[str, Any]:
        """Returns detected network interfaces, local IPs, and active tunnel state."""
        primary_ip = self.get_local_ip()
        interfaces: List[Dict[str, str]] = []

        try:
            for iface_name, addrs in psutil.net_if_addrs().items():
                if iface_name.startswith(("docker", "virbr", "veth", "br-")):
                    continue
                for addr in addrs:
                    if addr.family == socket.AF_INET and not addr.address.startswith("127."):
                        interfaces.append({
                            "interface": iface_name,
                            "ip": addr.address,
                            "url": f"http://{addr.address}:{port}"
                        })
        except Exception as e:
            logger.debug(f"Interface listing error: {e}")

        if not interfaces:
            interfaces.append({
                "interface": "local",
                "ip": primary_ip,
                "url": f"http://{primary_ip}:{port}"
            })

        local_url = f"http://{primary_ip}:{port}"
        return {
            "primary_ip": primary_ip,
            "lan_ip": primary_ip,
            "port": port,
            "local_url": local_url,
            "default_url": local_url,
            "interfaces": interfaces,
            "tunnel": self.get_tunnel_status()
        }

    def get_lan_ip(self) -> str:
        """Alias for get_local_ip."""
        return self.get_local_ip()

    def generate_qr(self, text: str) -> bytes:
        """Generates a crisp PNG QR code in memory."""
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=8,
            border=2,
        )
        qr.add_data(text)
        qr.make(fit=True)
        img = qr.make_image(fill_color="#0ea5e9", back_color="#090d16")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def get_tunnel_status(self) -> Dict[str, Any]:
        """Returns the active state of Cloudflare Quick Tunnel."""
        is_running = self._tunnel_proc is not None and self._tunnel_proc.returncode is None
        is_active = bool(is_running and self._tunnel_url)
        return {
            "active": is_active,
            "status": "running" if is_active else ("starting" if is_running else "stopped"),
            "url": self._tunnel_url if is_running else None,
            "public_url": self._tunnel_url if is_running else None,
            "error": self._tunnel_error if not is_running else None
        }

    async def start_cloudflare_tunnel(self, local_port: int = 8088) -> Dict[str, Any]:
        """
        Launches cloudflared quick tunnel to expose http://127.0.0.1:<local_port>
        over a secure public HTTPS trycloudflare.com URL without port forwarding.
        """
        if self._tunnel_proc and self._tunnel_proc.returncode is None and self._tunnel_url:
            return self.get_tunnel_status()

        await self.stop_cloudflare_tunnel()
        self._tunnel_error = None
        self._tunnel_url = None

        bin_path = CLOUDFLARED_BIN
        if not bin_path.exists() or not os.access(bin_path, os.X_OK):
            # Check system path
            import shutil
            sys_bin = shutil.which("cloudflared")
            if sys_bin:
                bin_path = Path(sys_bin)
            else:
                self._tunnel_error = "cloudflared binary not available"
                return {"active": False, "url": None, "error": self._tunnel_error}

        cmd = [
            str(bin_path),
            "tunnel",
            "--url",
            f"http://127.0.0.1:{local_port}",
            "--no-autoupdate"
        ]

        logger.info(f"Starting Cloudflare Quick Tunnel: {' '.join(cmd)}")
        try:
            self._tunnel_proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
        except Exception as e:
            self._tunnel_error = f"Failed to spawn cloudflared: {e}"
            logger.error(self._tunnel_error)
            return {"active": False, "url": None, "error": self._tunnel_error}

        # Monitor output to extract https://*.trycloudflare.com URL
        url_future: asyncio.Future[str] = asyncio.get_event_loop().create_future()

        async def read_stream(stream, name):
            url_pattern = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")
            while True:
                line = await stream.readline()
                if not line:
                    break
                decoded = line.decode("utf-8", errors="replace")
                match = url_pattern.search(decoded)
                if match and not url_future.done():
                    url_future.set_result(match.group(0))
                if "error" in decoded.lower():
                    logger.debug(f"[cloudflared {name}] {decoded.strip()}")

        stdout_task = asyncio.create_task(read_stream(self._tunnel_proc.stdout, "stdout"))
        stderr_task = asyncio.create_task(read_stream(self._tunnel_proc.stderr, "stderr"))

        try:
            extracted_url = await asyncio.wait_for(url_future, timeout=12.0)
            self._tunnel_url = extracted_url
            logger.info(f"Cloudflare Tunnel active: {extracted_url}")
        except asyncio.TimeoutError:
            self._tunnel_error = "Timeout waiting for trycloudflare.com URL"
            logger.warning(self._tunnel_error)
            await self.stop_cloudflare_tunnel()
        except Exception as e:
            self._tunnel_error = str(e)
            await self.stop_cloudflare_tunnel()

        return self.get_tunnel_status()

    async def stop_cloudflare_tunnel(self) -> Dict[str, Any]:
        """Terminates active Cloudflare Quick Tunnel."""
        if self._tunnel_proc:
            try:
                self._tunnel_proc.terminate()
                try:
                    await asyncio.wait_for(self._tunnel_proc.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    self._tunnel_proc.kill()
            except Exception as e:
                logger.debug(f"Tunnel termination notice: {e}")
            finally:
                self._tunnel_proc = None

        self._tunnel_url = None
        return {"active": False, "url": None, "error": None}


network_service = NetworkService()
