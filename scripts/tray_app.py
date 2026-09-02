#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TG Power Suite — Linux System Tray Companion (Static Icon Edition)
Displays a rock-solid static tray icon without panel re-draws or flickering.
Provides background desktop notifications and 1-click launchers for Dashboard, Cinema, Sniffer, and Settings.
"""

import os
import sys
import time
import socket
import urllib.request
import json
import webbrowser
import subprocess
import threading
from pathlib import Path
from typing import Optional
from PIL import Image, ImageDraw

try:
    import pystray
except ImportError:
    print("[ERROR] pystray is not installed. Run: pip install pystray")
    sys.exit(1)

# Application Paths
SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
ASSETS_DIR = ROOT_DIR / "frontend" / "assets"
if not ASSETS_DIR.exists():
    ASSETS_DIR = ROOT_DIR / "assets"

PORT = 8088
API_BASE = f"http://127.0.0.1:{PORT}/api"

# Single Instance Socket Lock
_LOCK_PORT = 49888
_lock_socket = None

def acquire_single_instance_lock() -> bool:
    global _lock_socket
    try:
        _lock_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        _lock_socket.bind(("127.0.0.1", _LOCK_PORT))
        _lock_socket.listen(1)
        return True
    except socket.error:
        return False

# Desktop Notification Helper
def send_notification(title: str, message: str, icon_path: Optional[str] = None):
    try:
        cmd = ["notify-send", "-a", "TG Power Suite", title, message]
        if icon_path and Path(icon_path).exists():
            cmd.extend(["-i", str(icon_path)])
        elif (ASSETS_DIR / "icon-192.png").exists():
            cmd.extend(["-i", str(ASSETS_DIR / "icon-192.png")])
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        print(f"[Notify] Failed to send notification: {e}")

# High-DPI Static Tray Icon
def get_static_tray_icon() -> Image.Image:
    """Loads a crisp, high-DPI static icon without dynamic redraw badges."""
    size = 64
    base_icon_path = ASSETS_DIR / "tg-power-suite.png"
    if not base_icon_path.exists():
        base_icon_path = ASSETS_DIR / "icon-192.png"
    if not base_icon_path.exists():
        base_icon_path = ASSETS_DIR / "tray_icon.png"

    if base_icon_path.exists():
        try:
            return Image.open(str(base_icon_path)).convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)
        except Exception:
            pass

    # Procedural fallback
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((4, 4, size - 4, size - 4), fill="#6c5ce7", outline="#00cec9", width=3)
    points = [(size * 0.25, size * 0.5), (size * 0.75, size * 0.25), (size * 0.55, size * 0.75), (size * 0.45, size * 0.55)]
    draw.polygon(points, fill="#ffffff")
    return img


class LinuxTrayApp:
    def __init__(self):
        self.icon = None
        self.is_running = True
        self.known_completed_task_ids = set()
        self.first_poll = True

    def fetch_api(self, endpoint: str, method: str = "GET", payload: Optional[dict] = None) -> Optional[dict]:
        try:
            url = f"{API_BASE}/{endpoint.lstrip('/')}"
            req = urllib.request.Request(url, method=method)
            req.add_header("Content-Type", "application/json")
            data_bytes = json.dumps(payload).encode("utf-8") if payload else None
            with urllib.request.urlopen(req, data=data_bytes, timeout=2.0) as response:
                if 200 <= response.status < 300:
                    return json.loads(response.read().decode("utf-8"))
        except Exception:
            return None
        return None

    def poll_notifications(self):
        """Background thread monitoring tasks ONLY for desktop notifications without touching icon image."""
        while self.is_running:
            try:
                tasks = self.fetch_api("/tasks")
                if isinstance(tasks, list):
                    for t in tasks:
                        t_id = t.get("id")
                        t_status = t.get("status")
                        t_filename = t.get("filename", "File")
                        t_chat = t.get("chat_name", "Telegram")

                        if t_id and t_status == "completed":
                            if not self.first_poll and t_id not in self.known_completed_task_ids:
                                send_notification(
                                    "🎉 Upload Complete",
                                    f"'{t_filename}' was successfully sent to {t_chat}!"
                                )
                            self.known_completed_task_ids.add(t_id)
                        elif t_id and t_status == "failed":
                            if not self.first_poll and t_id not in self.known_completed_task_ids:
                                err = t.get("error", "Unknown error")
                                send_notification("❌ Upload Failed", f"'{t_filename}': {err}")
                                self.known_completed_task_ids.add(t_id)

                    self.first_poll = False
            except Exception as e:
                print(f"[TrayApp] Polling notice: {e}")

            time.sleep(3.0)

    def open_url(self, path: str = ""):
        target = f"http://localhost:{PORT}/{path.lstrip('/')}"
        webbrowser.open(target)

    def pause_all_uploads(self):
        self.fetch_api("/upload/batch/pause", method="POST")
        send_notification("Uploads Paused", "All active uploads have been paused.")

    def resume_all_uploads(self):
        self.fetch_api("/upload/batch/resume", method="POST")
        send_notification("Uploads Resumed", "Resuming active upload queue...")

    def restart_backend_service(self):
        try:
            send_notification("TG Power Suite", "Restarting background service...")
            subprocess.run(["systemctl", "--user", "restart", "tg-power-suite.service"], check=False)
        except Exception as e:
            print(f"[TrayApp] Failed to restart service: {e}")

    def quit_tray(self):
        self.is_running = False
        if self.icon:
            self.icon.stop()
        os._exit(0)

    def build_menu(self):
        return pystray.Menu(
            pystray.MenuItem("🚀 Open TG Power Suite", lambda: self.open_url(""), default=True),
            pystray.MenuItem("🎬 Cinema & Streamer", lambda: self.open_url("#cinema")),
            pystray.MenuItem("📥 Sniffer & Proxy", lambda: self.open_url("#sniffer")),
            pystray.MenuItem("⚙️ Settings & Auth", lambda: self.open_url("#settings")),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("⏸️ Pause All Uploads", lambda: self.pause_all_uploads()),
            pystray.MenuItem("▶️ Resume All Uploads", lambda: self.resume_all_uploads()),
            pystray.MenuItem("🔄 Restart Backend Service", lambda: self.restart_backend_service()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("🚪 Quit Tray", lambda: self.quit_tray())
        )

    def run(self):
        if not acquire_single_instance_lock():
            print("[TrayApp] TG Power Suite Tray is already running. Exiting.")
            sys.exit(0)

        # Load static icon once
        static_img = get_static_tray_icon()
        self.icon = pystray.Icon(
            "tg_power_suite_tray",
            static_img,
            "TG Power Suite",
            self.build_menu()
        )

        # Start desktop notification monitor in background
        monitor_thread = threading.Thread(target=self.poll_notifications, daemon=True)
        monitor_thread.start()

        send_notification("TG Power Suite", "System Tray Active.")

        # Run tray loop (blocks main thread)
        self.icon.run()


if __name__ == "__main__":
    app = LinuxTrayApp()
    app.run()
