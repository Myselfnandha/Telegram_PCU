import os
import re
import sys
import subprocess
from pathlib import Path
import pytest
from httpx import AsyncClient, ASGITransport

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.main import app

ROOT_DIR = Path(__file__).resolve().parent.parent.parent


def test_root_launch_headless_path_resolution():
    script_path = ROOT_DIR / "launch_headless.sh"
    assert script_path.exists()
    content = script_path.read_text(encoding="utf-8")
    assert 'APP_DIR="$(cd "$SCRIPT_DIR" && pwd)"' in content or 'APP_DIR=' in content

    res = subprocess.run(["bash", "-n", str(script_path)], capture_output=True, text=True)
    assert res.returncode == 0

    run_res = subprocess.run(
        ["bash", "-c", f'SOURCE="{script_path}"; while [ -h "$SOURCE" ]; do DIR="$(cd -P "$(dirname "$SOURCE")" >/dev/null 2>&1 && pwd)"; SOURCE="$(readlink "$SOURCE")"; [[ $SOURCE != /* ]] && SOURCE="$DIR/$SOURCE"; done; SCRIPT_DIR="$(cd -P "$(dirname "$SOURCE")" >/dev/null 2>&1 && pwd)"; if [ -f "$SCRIPT_DIR/backend/run.py" ]; then APP_DIR="$(cd "$SCRIPT_DIR" && pwd)"; else APP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"; fi; echo -n "$APP_DIR"'],
        capture_output=True,
        text=True
    )
    assert Path(run_res.stdout) == ROOT_DIR


def test_scripts_launch_headless_path_resolution():
    script_path = ROOT_DIR / "scripts" / "launch_headless.sh"
    assert script_path.exists()
    content = script_path.read_text(encoding="utf-8")
    assert 'APP_DIR="$(cd "$SCRIPT_DIR" && pwd)"' in content or 'APP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"' in content

    res = subprocess.run(["bash", "-n", str(script_path)], capture_output=True, text=True)
    assert res.returncode == 0

    run_res = subprocess.run(
        ["bash", "-c", f'SOURCE="{script_path}"; while [ -h "$SOURCE" ]; do DIR="$(cd -P "$(dirname "$SOURCE")" >/dev/null 2>&1 && pwd)"; SOURCE="$(readlink "$SOURCE")"; [[ $SOURCE != /* ]] && SOURCE="$DIR/$SOURCE"; done; SCRIPT_DIR="$(cd -P "$(dirname "$SOURCE")" >/dev/null 2>&1 && pwd)"; if [ -f "$SCRIPT_DIR/backend/run.py" ]; then APP_DIR="$(cd "$SCRIPT_DIR" && pwd)"; else APP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"; fi; echo -n "$APP_DIR"'],
        capture_output=True,
        text=True
    )
    assert Path(run_res.stdout) == ROOT_DIR


def test_system_autoclean_removed():
    autoclean_dir = ROOT_DIR / "scripts" / "system-autoclean"
    assert not autoclean_dir.exists()


def test_stray_root_files_cleaned():
    stray_files = [
        ROOT_DIR / "anon.session",
        ROOT_DIR / "cinema_cache.db",
        ROOT_DIR / "tg_cinema.db",
        ROOT_DIR / "tg_power_suite_rc_v3.zip",
        ROOT_DIR / "tg_power_suite_v2.0_source.zip",
        ROOT_DIR / "tg_power_suite_v2.0_vlc_stability_patch.zip",
        ROOT_DIR / "tg_power_suite_v2.0_vlc_stability_patch",
    ]
    for path in stray_files:
        assert not path.exists()


def test_dockerfile_configuration():
    dockerfile_path = ROOT_DIR / "Dockerfile"
    assert dockerfile_path.exists()
    content = dockerfile_path.read_text(encoding="utf-8")
    assert "/api/system/health" in content
    assert "/api/system/stats" not in content
    assert "WORKDIR /app" in content
    assert 'CMD ["python", "backend/run.py", "start", "--foreground"]' in content


def test_docker_compose_configuration():
    compose_path = ROOT_DIR / "docker-compose.yml"
    assert compose_path.exists()
    content = compose_path.read_text(encoding="utf-8")
    assert "http://localhost:8088/api/system/health" in content
    assert "http://localhost:8088/api/system/stats" not in content


@pytest.mark.asyncio
async def test_healthcheck_endpoint_is_public():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/api/system/health")
        assert response.status_code == 200
        payload = response.json()
        assert payload.get("status") == "healthy" or payload.get("status") == "ok" or "healthy" in str(payload)


def test_systemd_service_unit():
    service_path = ROOT_DIR / "linux" / "tg-power-suite.service"
    assert service_path.exists()
    content = service_path.read_text(encoding="utf-8")
    assert "1001" not in content
    assert "DBUS_SESSION_BUS_ADDRESS" not in content
    assert "ExecStart" in content
    assert "WorkingDirectory" in content


def test_telegram_fdm_proxy_port_configuration():
    proxy_script = ROOT_DIR / "telegram-fdm-proxy" / "tg_fdm_proxy.py"
    assert proxy_script.exists()
    content = proxy_script.read_text(encoding="utf-8")
    assert "PROXY_PORT = 8089" in content
    assert 'os.getenv("PROXY_PORT", "8089")' in content
    assert "start: int = 8089" in content
    assert "default=8089" in content

    proxy_env = ROOT_DIR / "telegram-fdm-proxy" / ".env"
    if proxy_env.exists():
        env_content = proxy_env.read_text(encoding="utf-8")
        assert "PROXY_PORT=8089" in env_content


def test_telegram_fdm_proxy_cleanup():
    proxy_dir = ROOT_DIR / "telegram-fdm-proxy"
    build_dir = proxy_dir / "build"
    dist_dir = proxy_dir / "dist"
    assert not build_dir.exists()
    assert not dist_dir.exists()
    for log_file in proxy_dir.glob("*.log"):
        try:
            log_file.unlink()
        except OSError:
            pass
    assert len(list(proxy_dir.glob("*.log"))) == 0
    res = subprocess.run(["git", "-C", str(proxy_dir), "ls-files", "*.log"], capture_output=True, text=True)
    assert res.stdout.strip() == ""


def test_telegram_fdm_proxy_compiles():
    proxy_script = ROOT_DIR / "telegram-fdm-proxy" / "tg_fdm_proxy.py"
    res = subprocess.run([sys.executable, "-m", "py_compile", str(proxy_script)], capture_output=True, text=True)
    assert res.returncode == 0
    pycache = proxy_script.parent / "__pycache__"
    if pycache.exists():
        import shutil
        shutil.rmtree(pycache, ignore_errors=True)
