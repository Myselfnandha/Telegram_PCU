#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TG Power Suite — Linux System Tray Application
Provides real-time tray telemetry, desktop notifications, pause/resume controls,
and instant quick-launchers for Dashboard, Cinema, Sniffer, and Settings.
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
from typing import Optional, Dict, Any
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

# Dynamic Tray Icon Generation
def create_tray_icon(status: str = "idle") -> Image.Image:
    """
    Renders high-DPI tray icon with status badges:
    - 'idle': Standard Telegram neon emblem (ready)
    - 'uploading': Cyan pulse with upload arrow indicator
    - 'paused': Amber yellow badge
    - 'offline': Red offline badge
    """
    size = 128
    base_icon_path = ASSETS_DIR / "tg-power-suite.png"
    if not base_icon_path.exists():
        base_icon_path = ASSETS_DIR / "icon-192.png"
    if not base_icon_path.exists():
        base_icon_path = ASSETS_DIR / "tray_icon.png"

    if base_icon_path.exists():
        try:
            base_img = Image.open(str(base_icon_path)).convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)
        except Exception:
            base_img = None
    else:
        base_img = None

    if base_img is None:
        base_img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(base_img)
        draw.ellipse((8, 8, size - 8, size - 8), fill="#6c5ce7", outline="#00cec9", width=6)
        # Procedural paper plane
        points = [(size * 0.25, size * 0.5), (size * 0.75, size * 0.25), (size * 0.55, size * 0.75), (size * 0.45, size * 0.55)]
        draw.polygon(points, fill="#ffffff")

    # Add dynamic status badge
    draw = ImageDraw.Draw(base_img)
    badge_radius = 20
    badge_x = size - badge_radius - 6
    badge_y = size - badge_radius - 6

    if status == "uploading":
        # Cyan upload badge with arrow
        draw.ellipse((badge_x - badge_radius, badge_y - badge_radius, badge_x + badge_radius, badge_y + badge_radius), fill="#0984e3", outline="#ffffff", width=3)
        # White up arrow
        arrow = [
            (badge_x, badge_y - 10),
            (badge_x - 8, badge_y),
            (badge_x - 3, badge_y),
            (badge_x - 3, badge_y + 9),
            (badge_x + 3, badge_y + 9),
            (badge_x + 3, badge_y),
            (badge_x + 8, badge_y),
        ]
        draw.polygon(arrow, fill="#ffffff")
    elif status == "paused":
        # Amber paused badge
        draw.ellipse((badge_x - badge_radius, badge_y - badge_radius, badge_x + badge_radius, badge_y + badge_radius), fill="#fdcb6e", outline="#ffffff", width=3)
        draw.rectangle((badge_x - 6, badge_y - 7, badge_x - 2, badge_y + 7), fill="#2d3436")
        draw.rectangle((badge_x + 2, badge_y - 7, badge_x + 6, badge_y + 7), fill="#2d3436")
    elif status == "offline":
        # Red offline badge
        draw.ellipse((badge_x - badge_radius, badge_y - badge_radius, badge_x + badge_radius, badge_y + badge_radius), fill="#ff7675", outline="#ffffff", width=3)
        draw.line((badge_x - 6, badge_y - 6, badge_x + 6, badge_y + 6), fill="#ffffff", width=3)
        draw.line((badge_x + 6, badge_y - 6, badge_x - 6, badge_y + 6), fill="#ffffff", width=3)
    else:
        # Green online dot
        draw.ellipse((badge_x - 14, badge_y - 14, badge_x + 14, badge_y + 14), fill="#00b894", outline="#ffffff", width=3)

    return base_img.resize((64, 64), Image.Resampling.LANCZOS)


class LinuxTrayApp:
    def __init__(self):
        self.icon = None
        self.is_running = True
        self.backend_online = False
        self.active_tasks_count = 0
        self.current_speed_str = "0.0 KB/s"
        self.status_state = "offline"
        self.status_label = "Checking status..."
        self.known_completed_task_ids = set()
        self.first_poll = True

    def format_bytes_speed(self, speed_bytes: float) -> str:
        if speed_bytes >= 1024 * 1024:
            return f"{speed_bytes / (1024 * 1024):.1f} MB/s"
        elif speed_bytes >= 1024:
            return f"{speed_bytes / 1024:.1f} KB/s"
        else:
            return f"{int(speed_bytes)} B/s"

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

    def poll_backend_state(self):
        """Background thread monitoring telemetry and completed tasks."""
        while self.is_running:
            try:
                stats = self.fetch_api("/system/stats")
                tasks = self.fetch_api("/tasks")

                if stats is not None:
                    self.backend_online = True
                    active_tasks = stats.get("active_uploads", 0)
                    speed_bps = stats.get("current_speed_bps", 0.0)
                    self.current_speed_str = self.format_bytes_speed(speed_bps)
                    self.active_tasks_count = active_tasks

                    if active_tasks > 0:
                        self.status_state = "uploading"
                        self.status_label = f"🔵 {active_tasks} Active Upload{'s' if active_tasks > 1 else ''} ({self.current_speed_str})"
                    else:
                        self.status_state = "idle"
                        self.status_label = "🟢 Online & Idle (Ready)"
                else:
                    self.backend_online = False
                    self.status_state = "offline"
                    self.status_label = "🔴 Service Offline"

                # Check for newly completed tasks to trigger native desktop notification
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

                # Update tray icon & tooltip
                if self.icon:
                    new_img = create_tray_icon(self.status_state)
                    self.icon.icon = new_img
                    self.icon.title = f"TG Power Suite — {self.status_label}"
                    # Refresh menu labels
                    self.icon.update_menu()

            except Exception as e:
                print(f"[TrayApp] Polling notice: {e}")

            time.sleep(2.5)

    def open_url(self, path: str = ""):
        target = f"http://localhost:{PORT}/{path.lstrip('/')}"
        webbrowser.open(target)

    def toggle_pause_all(self):
        if self.status_state == "uploading":
            self.fetch_api("/upload/batch/pause", method="POST")
            send_notification("Uploads Paused", "All active uploads have been paused.")
        else:
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
            pystray.MenuItem(lambda text: f"Status: {self.status_label}", lambda: None, enabled=False),
            pystray.MenuItem(
                lambda text: "⏸️ Pause All Uploads" if self.status_state == "uploading" else "▶️ Resume Uploads",
                lambda: self.toggle_pause_all()
            ),
            pystray.MenuItem("🔄 Restart Backend Service", lambda: self.restart_backend_service()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("🚪 Quit Tray", lambda: self.quit_tray())
        )

    def run(self):
        if not acquire_single_instance_lock():
            print("[TrayApp] TG Power Suite Tray is already running. Exiting.")
            sys.exit(0)

        # Initial icon
        initial_img = create_tray_icon("idle")
        self.icon = pystray.Icon(
            "tg_power_suite_tray",
            initial_img,
            "TG Power Suite — Initializing...",
            self.build_menu()
        )

        # Start telemetry monitor thread
        monitor_thread = threading.Thread(target=self.poll_backend_state, daemon=True)
        monitor_thread.start()

        send_notification("TG Power Suite", "System Tray Active & Monitoring in background.")

        # Run tray loop (blocks main thread)
        self.icon.run()


if __name__ == "__main__":
    app = LinuxTrayApp()
    app.run()
