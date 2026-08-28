import os
import sys
import logging
import threading
import webbrowser
from typing import Optional
from PIL import Image, ImageDraw
from app.config import ASSETS_DIR, PORT

logger = logging.getLogger("tray_service")

_tray_icon = None
_tray_thread = None

def _create_fallback_icon_image(size: int = 128) -> Image.Image:
    """Create a high quality procedural tray icon if asset is not found."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # Vibrant circle background
    draw.ellipse((4, 4, size - 4, size - 4), fill="#6c5ce7", outline="#00cec9", width=max(2, size // 32))
    # Inner T shape
    draw.rectangle((size * 0.25, size * 0.25, size * 0.75, size * 0.38), fill="#ffffff")
    draw.rectangle((size * 0.44, size * 0.35, size * 0.56, size * 0.75), fill="#ffffff")
    return img

def get_tray_icon_image() -> Image.Image:
    """Loads tray icon image from assets or creates procedural fallback."""
    icon_paths = [
        ASSETS_DIR / "tray_icon.png",
        ASSETS_DIR / "tg-power-suite.png",
        ASSETS_DIR / "tg-fdm-proxy.png",
    ]
    for p in icon_paths:
        if p.exists():
            try:
                return Image.open(str(p)).resize((64, 64))
            except Exception:
                pass
    return _create_fallback_icon_image(64)


def start_tray_service(port: int = PORT) -> bool:
    """Starts the system tray icon in a daemon thread."""
    global _tray_icon, _tray_thread
    try:
        import pystray

        def on_open_dashboard(icon, item):
            webbrowser.open(f"http://127.0.0.1:{port}")

        def on_open_settings(icon, item):
            webbrowser.open(f"http://127.0.0.1:{port}#settings")

        def on_quit(icon, item):
            logger.info("Tray exit triggered.")
            icon.stop()
            os._exit(0)

        menu = pystray.Menu(
            pystray.MenuItem("🚀 TG Power Suite", on_open_dashboard, default=True),
            pystray.MenuItem("⚙️ Web Settings", on_open_settings),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("❌ Quit", on_quit)
        )

        image = get_tray_icon_image()
        _tray_icon = pystray.Icon(
            "tg_power_suite",
            image,
            "TG Power Suite",
            menu
        )

        def _run():
            try:
                _tray_icon.run()
            except Exception as e:
                logger.debug(f"System tray run error: {e}")

        _tray_thread = threading.Thread(target=_run, daemon=True)
        _tray_thread.start()
        logger.info("System Tray service started.")
        return True
    except Exception as e:
        logger.debug(f"Could not initialize system tray: {e}")
        return False

def stop_tray_service():
    """Stops system tray icon."""
    global _tray_icon
    if _tray_icon:
        try:
            _tray_icon.stop()
        except Exception:
            pass
        _tray_icon = None
