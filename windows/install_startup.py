import os
import sys
from pathlib import Path

def install_startup():
    if sys.platform != "win32":
        print("Windows startup installer is only for Windows systems.")
        return

    app_dir = Path(__file__).resolve().parent.parent
    startup_dir = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    
    if not startup_dir.exists():
        print(f"Startup directory not found: {startup_dir}")
        return

    vbs_path = startup_dir / "tg_power_suite_autostart.vbs"
    run_py = app_dir / "backend" / "run.py"
    
    vbs_content = f'''Set WshShell = CreateObject("WScript.Shell")
WshShell.Run "python \"{run_py}\" start --tray", 0, False
'''
    vbs_path.write_text(vbs_content, encoding="utf-8")
    print(f"✅ TG Power Suite autostart VBS installed to: {vbs_path}")

if __name__ == "__main__":
    install_startup()
