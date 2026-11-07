import sys
import os
import time
import signal
import asyncio
import tempfile
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from app.main import app
import run as run_module
from app.services.folder_watcher import FolderWatcherDaemon, folder_watcher
from app.services.queue_manager import queue_manager, UploadItem
from app.services.download_manager import download_manager


@pytest.fixture(autouse=True)
def reset_service_queues():
    queue_manager.queue = asyncio.Queue()
    queue_manager.tasks.clear()
    download_manager._queue = asyncio.Queue()
    yield
    queue_manager.queue = asyncio.Queue()
    queue_manager.tasks.clear()
    download_manager._queue = asyncio.Queue()


def test_tray_service_sigterm_and_lifespan_completion():
    backend_path = str(Path(__file__).parent.parent)
    script = f"""
import os, sys, time, threading, signal
sys.path.insert(0, {repr(backend_path)})
os.environ['TG_TEST_MODE'] = '1'
import uvicorn
from app.main import app

def trigger_tray_quit():
    time.sleep(1.0)
    os.kill(os.getpid(), signal.SIGTERM)

t = threading.Thread(target=trigger_tray_quit, daemon=True)
t.start()

uvicorn.run(app, host='127.0.0.1', port=8768, log_level='info')
"""
    env = dict(os.environ, PYTHONPATH=backend_path, TG_TEST_MODE="1")
    p = subprocess.Popen([sys.executable, "-c", script], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    start_t = time.time()
    try:
        out, _ = p.communicate(timeout=8)
        elapsed = time.time() - start_t
    finally:
        if p.poll() is None:
            p.kill()

    assert p.returncode in (0, -15, 1)
    assert elapsed < 7.5
    assert "Shutting down TG Power Suite backend..." in out
    assert "Shutdown complete." in out
    assert "Application shutdown complete." in out


def test_sigint_lifespan_completion():
    backend_path = str(Path(__file__).parent.parent)
    script = f"""
import os, sys, time, threading, signal
sys.path.insert(0, {repr(backend_path)})
os.environ['TG_TEST_MODE'] = '1'
import uvicorn
from app.main import app

def trigger_sigint():
    time.sleep(1.0)
    os.kill(os.getpid(), signal.SIGINT)

t = threading.Thread(target=trigger_sigint, daemon=True)
t.start()

uvicorn.run(app, host='127.0.0.1', port=8769, log_level='info')
"""
    env = dict(os.environ, PYTHONPATH=backend_path, TG_TEST_MODE="1")
    p = subprocess.Popen([sys.executable, "-c", script], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    start_t = time.time()
    try:
        out, _ = p.communicate(timeout=8)
        elapsed = time.time() - start_t
    finally:
        if p.poll() is None:
            p.kill()

    assert p.returncode in (0, -2, 1)
    assert elapsed < 7.5
    assert "Shutting down TG Power Suite backend..." in out
    assert "Shutdown complete." in out
    assert "Application shutdown complete." in out


def test_cli_stop_fast_exit(tmp_path):
    pid_file = tmp_path / "tg.pid"
    p_target = subprocess.Popen([sys.executable, "-c", f"""
import time, signal, sys
from pathlib import Path
pid_file = Path({repr(str(pid_file))})
def on_term(s, f):
    time.sleep(1.0)
    pid_file.unlink(missing_ok=True)
    sys.exit(0)
signal.signal(signal.SIGTERM, on_term)
while True:
    time.sleep(0.1)
"""], start_new_session=True)

    pid_file.write_text(str(p_target.pid))

    stop_script = f"""
import sys, time, subprocess
from pathlib import Path
sys.path.insert(0, {repr(str(Path(__file__).parent.parent))})
import run as run_module

run_module.PID_FILE = Path({repr(str(pid_file))})
run_module.DATA_DIR = Path({repr(str(tmp_path))})

orig_run = subprocess.run
def safe_run(cmd, *a, **kw):
    if any(x in cmd for x in ['systemctl', 'pkill', 'pgrep']):
        return subprocess.CompletedProcess(cmd, returncode=0, stdout='', stderr='')
    return orig_run(cmd, *a, **kw)
run_module.subprocess.run = safe_run

start_t = time.time()
run_module.cli_stop()
elapsed = time.time() - start_t
print(f'STOP_ELAPSED:{{elapsed:.2f}}')
"""
    res = subprocess.run([sys.executable, "-c", stop_script], capture_output=True, text=True)
    if p_target.poll() is None:
        p_target.kill()

    assert "STOP_ELAPSED:" in res.stdout
    elapsed = float(res.stdout.split("STOP_ELAPSED:")[1].strip())
    assert elapsed < 4.0
    assert not pid_file.exists()


def test_cli_stop_slow_exit(tmp_path):
    pid_file = tmp_path / "tg.pid"
    p_target = subprocess.Popen([sys.executable, "-c", f"""
import time, signal, sys
from pathlib import Path
pid_file = Path({repr(str(pid_file))})
def on_term(s, f):
    time.sleep(2.5)
    pid_file.unlink(missing_ok=True)
    sys.exit(0)
signal.signal(signal.SIGTERM, on_term)
while True:
    time.sleep(0.1)
"""], start_new_session=True)

    pid_file.write_text(str(p_target.pid))

    stop_script = f"""
import sys, time, subprocess
from pathlib import Path
sys.path.insert(0, {repr(str(Path(__file__).parent.parent))})
import run as run_module

run_module.PID_FILE = Path({repr(str(pid_file))})
run_module.DATA_DIR = Path({repr(str(tmp_path))})

orig_run = subprocess.run
def safe_run(cmd, *a, **kw):
    if any(x in cmd for x in ['systemctl', 'pkill', 'pgrep']):
        return subprocess.CompletedProcess(cmd, returncode=0, stdout='', stderr='')
    return orig_run(cmd, *a, **kw)
run_module.subprocess.run = safe_run

start_t = time.time()
run_module.cli_stop()
elapsed = time.time() - start_t
print(f'STOP_ELAPSED:{{elapsed:.2f}}')
"""
    res = subprocess.run([sys.executable, "-c", stop_script], capture_output=True, text=True)
    if p_target.poll() is None:
        p_target.kill()

    assert "STOP_ELAPSED:" in res.stdout
    elapsed = float(res.stdout.split("STOP_ELAPSED:")[1].strip())
    assert 2.0 <= elapsed < 5.0
    assert not pid_file.exists()


def test_cli_stop_hanging_process_fallback(tmp_path):
    pid_file = tmp_path / "tg.pid"
    p_target = subprocess.Popen([sys.executable, "-c", """
import time, signal, sys
signal.signal(signal.SIGTERM, signal.SIG_IGN)
while True:
    time.sleep(0.1)
"""], start_new_session=True)

    pid_file.write_text(str(p_target.pid))

    stop_script = f"""
import sys, time, subprocess
from pathlib import Path
sys.path.insert(0, {repr(str(Path(__file__).parent.parent))})
import run as run_module

run_module.PID_FILE = Path({repr(str(pid_file))})
run_module.DATA_DIR = Path({repr(str(tmp_path))})

orig_run = subprocess.run
def safe_run(cmd, *a, **kw):
    if any(x in cmd for x in ['systemctl', 'pkill', 'pgrep']):
        return subprocess.CompletedProcess(cmd, returncode=0, stdout='', stderr='')
    return orig_run(cmd, *a, **kw)
run_module.subprocess.run = safe_run

start_t = time.time()
run_module.cli_stop()
elapsed = time.time() - start_t
print(f'STOP_ELAPSED:{{elapsed:.2f}}')
"""
    res = subprocess.run([sys.executable, "-c", stop_script], capture_output=True, text=True)
    time.sleep(0.5)
    exitcode = p_target.poll()
    if p_target.poll() is None:
        p_target.kill()

    assert exitcode == -signal.SIGKILL
    assert "STOP_ELAPSED:" in res.stdout
    elapsed = float(res.stdout.split("STOP_ELAPSED:")[1].strip())
    assert elapsed >= 5.5


def test_cli_stop_stale_pid_cleanup(tmp_path, monkeypatch):
    pid_file = tmp_path / "tg.pid"
    pid_file.write_text("99999999")
    monkeypatch.setattr(run_module, "PID_FILE", pid_file)
    monkeypatch.setattr(run_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(run_module, "get_running_pid", lambda: None)
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(a[0], returncode=0, stdout="", stderr=""))

    run_module.cli_stop()
    assert not pid_file.exists()


@pytest.mark.asyncio
async def test_folder_watcher_reentrance_start_and_stop(tmp_path):
    fw = FolderWatcherDaemon()
    fw._watch_dir = tmp_path

    for _ in range(10):
        fw.start()
    assert fw._task is not None
    assert fw.is_enabled is True
    original_task = fw._task

    fw.start()
    assert fw._task is original_task

    for _ in range(5):
        await fw.stop()
    assert fw._task is None
    assert fw.is_enabled is False


@pytest.mark.asyncio
async def test_folder_watcher_rapid_interleaved_toggles(tmp_path):
    fw = FolderWatcherDaemon()
    fw._watch_dir = tmp_path

    for _ in range(25):
        fw.start()
        assert fw.is_enabled is True
        assert fw._task is not None
        await fw.stop()
        assert fw.is_enabled is False
        assert fw._task is None


@pytest.mark.asyncio
async def test_folder_watcher_concurrent_toggles(tmp_path):
    fw = FolderWatcherDaemon()
    fw._watch_dir = tmp_path

    async def toggle(idx):
        if idx % 2 == 0:
            fw.start()
        else:
            await fw.stop()

    await asyncio.gather(*(toggle(i) for i in range(20)))
    await fw.stop()
    assert fw.is_enabled is False
    assert fw._task is None


@pytest.mark.asyncio
async def test_folder_watcher_update_config_behavior(tmp_path):
    fw = FolderWatcherDaemon()
    fw._watch_dir = tmp_path

    fw.update_config(enabled=True)
    assert fw.is_enabled is True
    assert fw._task is not None

    fw.update_config(enabled=False)
    await asyncio.sleep(0.05)
    assert fw.is_enabled is False
    assert fw._task is None

    fw.update_config(enabled=True)
    assert fw.is_enabled is True
    assert fw._task is not None

    await fw.stop()


@pytest.mark.asyncio
async def test_folder_watcher_rapid_config_toggling_cancellation_race(tmp_path):
    fw = FolderWatcherDaemon()
    fw._watch_dir = tmp_path

    fw.update_config(enabled=False)
    fw.update_config(enabled=True)
    await asyncio.sleep(0.05)
    assert fw.is_enabled is True
    assert fw._task is not None and not fw._task.done()
    await fw.stop()


@pytest.mark.asyncio
async def test_lifespan_zero_coroutine_leak():
    os.environ["TG_TEST_MODE"] = "1"
    curr = asyncio.current_task()

    async with app.router.lifespan_context(app):
        active_tasks = [t for t in asyncio.all_tasks() if t is not curr and not t.done()]
        assert len(active_tasks) >= 4

    remaining = [t for t in asyncio.all_tasks() if t is not curr and not t.done()]
    assert len(remaining) == 0


@pytest.mark.asyncio
async def test_lifespan_with_folder_watcher_and_active_queues_zero_leak(tmp_path, monkeypatch):
    os.environ["TG_TEST_MODE"] = "1"
    curr = asyncio.current_task()

    async def fake_upload(*a, **kw):
        await asyncio.sleep(10)

    monkeypatch.setattr("app.telegram_client.TelegramClientManager.get_active_streaming_clients", AsyncMock(return_value=[MagicMock()]))
    monkeypatch.setattr("app.telegram_client.TelegramClientManager.get_client", AsyncMock(return_value=MagicMock()))
    monkeypatch.setattr(queue_manager, "_process_upload", fake_upload)

    async with app.router.lifespan_context(app):
        folder_watcher.start()
        item = UploadItem(
            task_id="test_leak_check",
            file_path=tmp_path / "dummy.bin",
            original_filename="dummy.bin",
            chat_id="me"
        )
        await queue_manager.add_task(item)
        await asyncio.sleep(0.05)

    remaining = [t for t in asyncio.all_tasks() if t is not curr and not t.done()]
    assert len(remaining) == 0


@pytest.mark.asyncio
async def test_repeated_lifespan_cycles_zero_leak():
    os.environ["TG_TEST_MODE"] = "1"
    curr = asyncio.current_task()

    for _ in range(5):
        async with app.router.lifespan_context(app):
            pass
        remaining = [t for t in asyncio.all_tasks() if t is not curr and not t.done()]
        assert len(remaining) == 0


@pytest.mark.asyncio
async def test_lifespan_folder_watcher_disabled_while_sleeping_zero_leak():
    os.environ["TG_TEST_MODE"] = "1"
    curr = asyncio.current_task()

    async with app.router.lifespan_context(app):
        folder_watcher.start()
        await asyncio.sleep(0.01)
        folder_watcher.update_config(enabled=False)

    remaining = [t for t in asyncio.all_tasks() if t is not curr and not t.done()]
    assert len(remaining) == 0


@pytest.mark.asyncio
async def test_folder_watcher_async_config_update(tmp_path):
    fw = FolderWatcherDaemon()
    fw._watch_dir = tmp_path
    res = await fw.update_config_async(enabled=True)
    assert res["enabled"] is True
    assert fw.is_enabled is True
    assert fw._task is not None and not fw._task.done()

    res2 = await fw.update_config_async(enabled=False)
    assert res2["enabled"] is False
    assert fw.is_enabled is False
    assert fw._task is None

