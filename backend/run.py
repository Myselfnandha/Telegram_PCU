#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TG Power Suite — Unified High-Speed Telegram MTProto Toolkit
Includes Turbo Uploader, FDM HTTP Streaming Proxy, Channel Sniffer, Remote Downloader, Web UI & Tray.
"""

import os
import sys
import time
import signal
import logging
import logging.handlers
import uvicorn
import argparse
import subprocess
import faulthandler
from pathlib import Path

try:
    faulthandler.register(signal.SIGUSR1, all_threads=True)
except Exception:
    pass

# Ensure backend directory is on sys.path
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app.config import HOST, PORT, DATA_DIR, BACKEND_DIR, ROOT_DIR
from app.services.tray_service import start_tray_service, stop_tray_service

PID_FILE = DATA_DIR / "tg_power_suite.pid"
LOG_FILE = DATA_DIR / "tg_power_suite.log"

class EndpointFilter(logging.Filter):
    """Silences routine UI background polling pings from cluttering terminal logs."""
    SILENT_PATHS = (
        "/api/governor/status",
        "/api/auth/status",
        "/api/history",
        "/api/system/network",
        "/socket.io/",
    )

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return not any(path in msg for path in self.SILENT_PATHS)

def setup_file_logging():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] [%(name)s] %(message)s")

    # 1. Persistent Rotating File Handler
    file_handler = logging.handlers.RotatingFileHandler(
        str(LOG_FILE),
        maxBytes=10 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    root_logger.addHandler(file_handler)

    # 2. Live Terminal Console Stream Handler (filtered)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(fmt)
    root_logger.addHandler(console_handler)

    # 3. Suppress polling noise from uvicorn HTTP access logger
    logging.getLogger("uvicorn.access").addFilter(EndpointFilter())

    logging.getLogger("telethon.network").setLevel(logging.ERROR)

def get_running_pid():
    """Returns PID of active process if alive, else None."""
    if not PID_FILE.exists():
        return None
    try:
        pid = int(PID_FILE.read_text().strip())
        if sys.platform != "win32":
            os.kill(pid, 0)
            return pid
        else:
            import psutil
            if psutil.pid_exists(pid):
                return pid
    except (ValueError, OSError, ProcessLookupError):
        pass
    except Exception:
        pass
    return None

def acquire_pid_lock():
    pid = get_running_pid()
    if pid and pid != os.getpid():
        return False, pid
    try:
        PID_FILE.write_text(str(os.getpid()))
        return True, os.getpid()
    except Exception:
        return True, os.getpid()

def release_pid_lock():
    try:
        if PID_FILE.exists():
            if PID_FILE.read_text().strip() == str(os.getpid()):
                PID_FILE.unlink(missing_ok=True)
    except Exception:
        pass

def cli_status():
    pid = get_running_pid()
    if pid:
        print(f"\n  ✅ TG Power Suite is RUNNING (PID: {pid})")
        print(f"  • Web Dashboard : http://localhost:{PORT}")
        print(f"  • Log File      : {LOG_FILE}\n")
    else:
        # Check systemd user service
        if sys.platform != "win32":
            res = subprocess.run(["systemctl", "--user", "is-active", "tg-power-suite.service"], capture_output=True, text=True)
            if res.stdout.strip() == "active":
                print(f"\n  ✅ TG Power Suite is RUNNING via systemd")
                print(f"  • Web Dashboard : http://localhost:{PORT}\n")
                return
        print("\n  ⭕ TG Power Suite is NOT running.\n")

def cli_stop():
    pid = get_running_pid()
    if sys.platform != "win32":
        # Also check systemd service
        subprocess.run(["systemctl", "--user", "stop", "tg-power-suite.service"], capture_output=True)

    if not pid:
        print("  ✅ TG Power Suite stopped.\n")
        return

    print(f"  🛑 Stopping TG Power Suite (PID: {pid})...")
    try:
        if sys.platform != "win32":
            os.kill(pid, signal.SIGTERM)
        else:
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
        time.sleep(1)
        if get_running_pid():
            if sys.platform != "win32":
                os.kill(pid, signal.SIGKILL)
        print("  ✅ Successfully stopped.\n")
    except Exception as e:
        print(f"  ❌ Error stopping process: {e}\n")

def cli_restart(enable_tray: bool = False, daemon: bool = True):
    print("  🔄 Restarting TG Power Suite...")
    cli_stop()
    time.sleep(1)
    if daemon:
        cmd = [sys.executable, str(Path(__file__).resolve()), "start", "--daemon"]
        if enable_tray:
            cmd.append("--tray")
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        print("  🚀 TG Power Suite restarted in background.\n")
    else:
        run_server(enable_tray=enable_tray)

def cli_logs(follow: bool = False, lines: int = 50):
    if LOG_FILE.exists():
        if follow and sys.platform != "win32":
            try:
                subprocess.run(["tail", "-n", str(lines), "-f", str(LOG_FILE)])
            except KeyboardInterrupt:
                pass
        else:
            content = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
            for l in content[-lines:]:
                print(l)
    elif sys.platform != "win32":
        # Read from systemd journal
        cmd = ["journalctl", "--user", "-u", "tg-power-suite.service", "-n", str(lines), "--no-pager"]
        if follow:
            cmd.append("-f")
        try:
            subprocess.run(cmd)
        except KeyboardInterrupt:
            pass
    else:
        print("  ⭕ No log file found.")

def run_server(enable_tray: bool = False):
    ok, active_pid = acquire_pid_lock()
    if not ok:
        print(f"\n❌ Error: TG Power Suite is already running with PID {active_pid}!")
        sys.exit(1)

    setup_file_logging()

    print("\n" + "=" * 60)
    print("  🚀 TG Power Suite — Starting High-Speed Server")
    print("=" * 60)
    print(f"  • Web UI Dashboard : http://localhost:{PORT}")
    print(f"  • Host / Port      : {HOST}:{PORT}")
    print(f"  • Desktop Tray     : {'Enabled' if enable_tray else 'Disabled'}")
    print(f"  • PID File         : {PID_FILE}")
    print("=" * 60 + "\n")

    if enable_tray:
        start_tray_service(port=PORT)

    try:
        uvicorn.run(
            "app.main:socket_app",
            host=HOST,
            port=PORT,
            log_level="info",
            access_log=True,
            reload=False
        )
    finally:
        if enable_tray:
            stop_tray_service()
        release_pid_lock()

def main():
    parser = argparse.ArgumentParser(description="TG Power Suite CLI")
    subparsers = parser.add_subparsers(dest="command")

    # start
    p_start = subparsers.add_parser("start", help="Start TG Power Suite server")
    p_start.add_argument("--tray", action="store_true", help="Enable system tray icon")
    p_start.add_argument("--daemon", action="store_true", help="Run detached in background")

    # stop
    subparsers.add_parser("stop", help="Stop running TG Power Suite server")

    # restart
    p_restart = subparsers.add_parser("restart", help="Restart TG Power Suite server")
    p_restart.add_argument("--tray", action="store_true", help="Enable system tray icon")
    p_restart.add_argument("--daemon", action="store_true", default=True, help="Run detached in background")

    # status
    subparsers.add_parser("status", help="Check running status")

    # logs
    p_logs = subparsers.add_parser("logs", help="View application logs")
    p_logs.add_argument("-f", "--follow", action="store_true", help="Follow live logs")
    p_logs.add_argument("-n", "--lines", type=int, default=50, help="Number of lines")

    args, unknown = parser.parse_known_args()

    if args.command == "stop":
        cli_stop()
    elif args.command == "restart":
        tray_flag = getattr(args, "tray", False) or ("--tray" in sys.argv)
        cli_restart(enable_tray=tray_flag, daemon=True)
    elif args.command == "status":
        cli_status()
    elif args.command == "logs":
        cli_logs(follow=args.follow, lines=args.lines)
    elif args.command == "start" or args.command is None:
        tray_flag = getattr(args, "tray", False) or ("--tray" in sys.argv)
        daemon_flag = getattr(args, "daemon", False) or ("--daemon" in sys.argv)

        if daemon_flag:
            cmd = [sys.executable, str(Path(__file__).resolve())]
            if tray_flag:
                cmd.append("--tray")
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            print("  🚀 TG Power Suite started in background.")
            return

        run_server(enable_tray=tray_flag)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
