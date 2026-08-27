#!/usr/bin/env python3
"""
Uvicorn Server Runner for Telegram Web Uploader.
Configured with automatic port conflict resolution, unlimited keep-alive,
and large request streaming limits for multi-gigabyte uploads.
"""

import sys
import socket
import uvicorn
from app.config import HOST, PORT

def is_port_in_use(port: int, host: str = "0.0.0.0") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((host, port))
            return False
        except OSError:
            return True

def find_available_port(start_port: int, host: str = "0.0.0.0", max_attempts: int = 20) -> int:
    for port in range(start_port, start_port + max_attempts):
        if not is_port_in_use(port, host):
            return port
    return start_port

if __name__ == "__main__":
    target_port = PORT

    if is_port_in_use(target_port, HOST):
        fallback_port = find_available_port(target_port + 1, HOST)
        print(f"\n[!] Port {target_port} is already in use by another application.")
        print(f"[+] Automatically switching to available port: {fallback_port}\n")
        target_port = fallback_port

    print("=" * 65)
    print(f" 🚀 Telegram Web Uploader is RUNNING")
    print(f" 👉 Open in Browser: http://localhost:{target_port}")
    print(f" 👉 Network Access:  http://{HOST}:{target_port}")
    print("=" * 65)

    uvicorn.run(
        "app.main:socket_app",
        host=HOST,
        port=target_port,
        reload=False,
        timeout_keep_alive=3600,       # 1 hour keep-alive for large uploads
        limit_concurrency=100,
        log_level="info",
        access_log=True
    )
