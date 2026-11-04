import os
import sys
import shutil
import logging
import asyncio
import subprocess
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("manager_detector")

USERNAME = os.environ.get("USERNAME", os.environ.get("USER", "user"))

MANAGER_COMMANDS = {
    "fdm": ["{exe}", "{url}"],
    "idm": ["{exe}", "/d", "{url}"],
    "neat": ["{exe}", "{url}"],
    "aria2": ["aria2c", "{url}"],
    "persepolis": ["persepolis", "{url}"],
    "kget": ["kget", "{url}"],
}

MANAGER_LABELS = {
    "fdm": "Free Download Manager",
    "aria2": "aria2 Multi-Thread Engine",
    "persepolis": "Persepolis Download Manager",
    "kget": "KGet Downloader",
    "idm": "Internet Download Manager",
    "neat": "Neat Download Manager",
    "direct": "Direct HTTP Stream",
}

MANAGER_EXE_NAMES = {
    "fdm": "fdm.exe",
    "idm": "IDMan.exe",
    "neat": "NeatDM.exe",
    "aria2": "aria2c.exe",
}

_FALLBACK_PATHS = {
    "fdm": [
        rf"C:\Users\{USERNAME}\AppData\Local\Softdeluxe\Free Download Manager\fdm.exe",
        r"C:\Program Files\Softdeluxe\Free Download Manager\fdm.exe",
        r"C:\Program Files (x86)\Softdeluxe\Free Download Manager\fdm.exe",
        r"C:\Program Files\Free Download Manager\fdm.exe",
        r"C:\Program Files (x86)\Free Download Manager\fdm.exe",
    ],
    "idm": [
        r"C:\Program Files (x86)\Internet Download Manager\IDMan.exe",
        r"C:\Program Files\Internet Download Manager\IDMan.exe",
    ],
    "neat": [
        rf"C:\Users\{USERNAME}\AppData\Local\Neat Download Manager\NeatDM.exe",
        r"C:\Program Files\Neat Download Manager\NeatDM.exe",
        r"C:\Program Files (x86)\Neat Download Manager\NeatDM.exe",
    ],
}

def detect_managers() -> Dict[str, str]:
    """Dynamically scan for all supported download managers on Linux and Windows."""
    found: Dict[str, str] = {}

    if sys.platform != "win32":
        # 1. Check FDM via Flatpak
        try:
            res = subprocess.run(
                ["flatpak", "info", "org.freedownloadmanager.Manager"],
                capture_output=True, text=True, timeout=2
            )
            if res.returncode == 0:
                found["fdm"] = "flatpak"
                logger.info("[OK] Found FDM via Flatpak (org.freedownloadmanager.Manager)")
        except Exception:
            pass

        # 2. Check native binaries on PATH
        linux_bins = {
            "fdm": ["freedownloadmanager", "fdm", "/opt/freedownloadmanager/fdm"],
            "aria2": ["aria2c"],
            "persepolis": ["persepolis"],
            "kget": ["kget"],
            "neat": ["neatdm", "neatdm.exe"],
        }
        for mgr_id, bins in linux_bins.items():
            if mgr_id in found:
                continue
            for b in bins:
                path = shutil.which(b) or (b if os.path.isfile(b) and os.access(b, os.X_OK) else None)
                if path:
                    found[mgr_id] = path
                    logger.info(f"[OK] Found {mgr_id.upper()} at: {path}")
                    break
    else:
        for mgr_id in ("fdm", "idm", "neat", "aria2"):
            exe_name = MANAGER_EXE_NAMES.get(mgr_id, f"{mgr_id}.exe")
            path = shutil.which(exe_name)
            if path:
                found[mgr_id] = path
                logger.info(f"[OK] Found {mgr_id.upper()} via PATH: {path}")
                continue

            for fb in _FALLBACK_PATHS.get(mgr_id, []):
                if os.path.isfile(fb):
                    found[mgr_id] = fb
                    logger.info(f"[OK] Found {mgr_id.upper()} via fallback: {fb}")
                    break

    return found


def is_manager_running(manager_id: str) -> bool:
    """Returns True if the download manager application is actively running."""
    if sys.platform != "win32":
        try:
            import psutil
            patterns = {
                "fdm": ["freedownloadmanager", "fdm", "org.freedownloadmanager.manager"],
                "aria2": ["aria2c"],
                "persepolis": ["persepolis"],
                "kget": ["kget"],
                "neat": ["neatdm"],
            }
            target_patterns = patterns.get(manager_id, [manager_id])
            for proc in psutil.process_iter(['name', 'cmdline']):
                try:
                    name = (proc.info['name'] or "").lower()
                    cmdline = " ".join(proc.info['cmdline'] or []).lower()
                    for pat in target_patterns:
                        if pat in name or pat in cmdline:
                            return True
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue
        except ImportError:
            try:
                res = subprocess.run(["pgrep", "-f", manager_id], capture_output=True, text=True)
                return res.returncode == 0
            except Exception:
                pass
        return False
    else:
        proc_name = MANAGER_EXE_NAMES.get(manager_id, "")
        if not proc_name:
            return False
        try:
            res = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {proc_name}", "/NH"],
                capture_output=True, text=True, timeout=3
            )
            return proc_name.lower() in res.stdout.lower()
        except Exception:
            return False


def get_desktop_env() -> Dict[str, str]:
    env = os.environ.copy()
    if sys.platform == "win32":
        return env

    try:
        out = subprocess.check_output(["systemctl", "--user", "show-environment"], text=True, timeout=1.5)
        for line in out.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                if k in ("DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS", "XDG_CURRENT_DESKTOP", "DESKTOP_SESSION") and v:
                    env[k] = v
    except Exception:
        pass

    if "DISPLAY" not in env and "WAYLAND_DISPLAY" not in env:
        try:
            my_uid = os.getuid() if hasattr(os, "getuid") else None
            for pid_entry in os.listdir("/proc"):
                if not pid_entry.isdigit():
                    continue
                try:
                    proc_path = f"/proc/{pid_entry}"
                    if my_uid is not None and os.stat(proc_path).st_uid != my_uid:
                        continue
                    with open(f"{proc_path}/environ", "rb") as f:
                        pairs = f.read().split(b"\0")
                    proc_env = {}
                    for p in pairs:
                        if b"=" in p:
                            k_b, v_b = p.split(b"=", 1)
                            proc_env[k_b.decode("utf-8", "ignore")] = v_b.decode("utf-8", "ignore")
                    if "DISPLAY" in proc_env or "WAYLAND_DISPLAY" in proc_env:
                        for key in ("DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "DBUS_SESSION_BUS_ADDRESS", "XDG_CURRENT_DESKTOP"):
                            if key in proc_env and proc_env[key]:
                                env.setdefault(key, proc_env[key])
                        if "DISPLAY" in env or "WAYLAND_DISPLAY" in env:
                            break
                except Exception:
                    continue
        except Exception:
            pass

    if "DISPLAY" not in env and "WAYLAND_DISPLAY" not in env:
        import glob
        x11_sockets = glob.glob("/tmp/.X11-unix/X*")
        if x11_sockets:
            disp_num = os.path.basename(x11_sockets[0])[1:]
            env["DISPLAY"] = f":{disp_num}"
        else:
            env["DISPLAY"] = ":0"

    if "XAUTHORITY" not in env:
        xauth_home = os.path.expanduser("~/.Xauthority")
        if os.path.exists(xauth_home):
            env["XAUTHORITY"] = xauth_home
        else:
            import glob
            my_uid = os.getuid() if hasattr(os, "getuid") else None
            for xf in glob.glob("/tmp/xauth_*"):
                try:
                    if my_uid is None or os.stat(xf).st_uid == my_uid:
                        env["XAUTHORITY"] = xf
                        break
                except Exception:
                    pass

    uid = os.getuid() if hasattr(os, "getuid") else 1000
    if "XDG_RUNTIME_DIR" not in env:
        env["XDG_RUNTIME_DIR"] = f"/run/user/{uid}"

    if "DBUS_SESSION_BUS_ADDRESS" not in env:
        bus_path = f"/run/user/{uid}/bus"
        if os.path.exists(bus_path):
            env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={bus_path}"

    return env


_get_desktop_env = get_desktop_env


async def ensure_manager_running(manager_id: str, installed: Dict[str, str]) -> bool:
    """Launch manager if installed but not running."""
    if manager_id in ("aria2", "direct"):
        return True

    exe = installed.get(manager_id)
    if not exe or is_manager_running(manager_id):
        return True

    cmd = ["flatpak", "run", "org.freedownloadmanager.Manager"] if (sys.platform != "win32" and exe == "flatpak") else [exe]
    logger.info(f"[{manager_id.upper()}] Launching manager: {cmd}")
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=_get_desktop_env())
    except Exception as e:
        logger.error(f"[{manager_id.upper()}] Failed to launch: {e}")
        return False

    for _ in range(8):
        await asyncio.sleep(0.5)
        if is_manager_running(manager_id):
            return True
    return False


async def trigger_manager(manager_id: str, url: str, installed: Optional[Dict[str, str]] = None) -> bool:
    """Dispatch download URL to the target download manager."""
    if installed is None:
        installed = detect_managers()

    exe = installed.get(manager_id)
    if not exe:
        return False

    await ensure_manager_running(manager_id, installed)
    desktop_env = _get_desktop_env()

    if sys.platform != "win32":
        if manager_id == "fdm" and exe == "flatpak":
            cmd = ["flatpak", "run", "org.freedownloadmanager.Manager", url]
        elif manager_id == "aria2":
            downloads_dir = os.path.expanduser("~/Downloads")
            cmd = ["aria2c", "-s", "16", "-x", "16", "-k", "1M", "--dir", downloads_dir, url]
            try:
                subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True, env=desktop_env)
                logger.info(f"[ARIA2] Started background download: {url}")
                return True
            except Exception as e:
                logger.error(f"[ARIA2] Launch failed: {e}")
                return False
        else:
            cmd_template = MANAGER_COMMANDS.get(manager_id, ["{exe}", "{url}"])
            cmd = [part.format(exe=exe, url=url) for part in cmd_template]
    else:
        cmd_template = MANAGER_COMMANDS.get(manager_id, ["{exe}", "{url}"])
        cmd = [part.format(exe=exe, url=url) for part in cmd_template]

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=desktop_env,
            start_new_session=True,
        )
        logger.info(f"[{manager_id.upper()}] Triggered download (PID: {proc.pid}): {url}")
        return True
    except Exception as e:
        logger.error(f"[{manager_id.upper()}] Failed to trigger: {e}")
        return False


async def auto_send(url: str, preferred: Optional[str] = None) -> Tuple[str, bool]:
    """Try installed managers in priority order, checking preferred manager first."""
    installed = detect_managers()
    pref = (preferred or os.getenv("PREFERRED_MANAGER", "auto")).strip().lower()
    
    if pref and pref in installed:
        ok = await trigger_manager(pref, url, installed)
        if ok:
            return pref, True

    for mgr in ("fdm", "aria2", "persepolis", "kget", "idm", "neat"):
        if mgr in installed:
            ok = await trigger_manager(mgr, url, installed)
            if ok:
                return mgr, True

    return "direct", False
